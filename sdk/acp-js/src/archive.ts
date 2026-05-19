/**
 * Produce a verifiable archive bundle by polling an ACP deployment.
 *
 * Mirrors the Python implementation byte-for-byte: same endpoints, same
 * directory layout, same idempotency. After archiving, the bundle is
 * verifiable offline by either `acp verify-bundle` (Python) or the same
 * subcommand in this TS CLI.
 */
import { mkdirSync, writeFileSync, existsSync } from "node:fs";
import path from "node:path";

export class ArchiveError extends Error {
  constructor(msg: string) {
    super(msg);
    this.name = "ArchiveError";
  }
}

export interface BuildArchiveOptions {
  baseUrl: string;
  token: string;
  outDir: string;
  tenant?: string;
  since?: string;
  until?: string;
  limit?: number;
  /** Override fetch (useful for tests). */
  fetchImpl?: typeof fetch;
}

export interface ArchiveCounts {
  receipts: number;
  inclusion: number;
  roots: number;
}

export async function buildArchive(opts: BuildArchiveOptions): Promise<ArchiveCounts> {
  const baseUrl = opts.baseUrl.replace(/\/$/, "");
  const out = opts.outDir;
  const fetchFn = opts.fetchImpl ?? fetch;
  const limit = opts.limit ?? 10_000;

  for (const d of [out, path.join(out, "receipts"), path.join(out, "inclusion"), path.join(out, "roots")]) {
    mkdirSync(d, { recursive: true });
  }

  const headers: Record<string, string> = {
    Authorization: `Bearer ${opts.token}`,
    "User-Agent": "acp-archive/0.2",
  };
  if (opts.tenant) headers["X-Tenant-ID"] = opts.tenant;

  // 1. Public key
  await writePubkey(fetchFn, baseUrl, headers, path.join(out, "public_key.pem"));

  // 2. Stream the export. Each line → maybe one receipt + maybe one inclusion.
  const seenDates = new Set<string>();
  const counts: ArchiveCounts = { receipts: 0, inclusion: 0, roots: 0 };

  const params = new URLSearchParams({ limit: String(limit) });
  if (opts.since) params.set("since", opts.since);
  if (opts.until) params.set("until", opts.until);

  const resp = await fetchFn(`${baseUrl}/v1/audit/export?${params.toString()}`, { headers });
  if (resp.status === 401 || resp.status === 403) {
    throw new ArchiveError(`/v1/audit/export: authentication failed (${resp.status})`);
  }
  if (resp.status >= 500) {
    throw new ArchiveError(`/v1/audit/export: server error ${resp.status}`);
  }
  if (resp.status !== 200) {
    throw new ArchiveError(`/v1/audit/export → ${resp.status}`);
  }
  const text = await resp.text();
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    let row: Record<string, unknown>;
    try {
      row = JSON.parse(line);
    } catch {
      continue;
    }
    const execId = row.id as string | undefined;
    const ts = (row.timestamp as string | undefined) ?? "";
    if (!execId) continue;
    seenDates.add(ts.slice(0, 10));

    const rPath = path.join(out, "receipts", `${execId}.json`);
    if (!existsSync(rPath)) {
      await saveReceipt(fetchFn, baseUrl, headers, execId, rPath);
      counts.receipts++;
    }

    const iPath = path.join(out, "inclusion", `${execId}.json`);
    if (!existsSync(iPath)) {
      if (await saveInclusion(fetchFn, baseUrl, headers, execId, iPath)) {
        counts.inclusion++;
      }
    }
  }

  // 3. Signed daily roots for every date seen in the export.
  for (const d of [...seenDates].sort()) {
    if (!d) continue;
    const rootPath = path.join(out, "roots", `${d}.json`);
    if (existsSync(rootPath)) continue;
    if (await saveRoot(fetchFn, baseUrl, headers, d, rootPath)) {
      counts.roots++;
    }
  }

  return counts;
}

// ── helpers ───────────────────────────────────────────────────────────────

async function writePubkey(
  fetchFn: typeof fetch,
  baseUrl: string,
  headers: Record<string, string>,
  filePath: string
): Promise<void> {
  const resp = await fetchFn(`${baseUrl}/v1/receipts/key`, { headers });
  if (resp.status === 401 || resp.status === 403)
    throw new ArchiveError(`/v1/receipts/key: authentication failed (${resp.status})`);
  if (resp.status >= 500)
    throw new ArchiveError(`/v1/receipts/key: server error ${resp.status}`);
  if (resp.status !== 200) throw new ArchiveError(`/v1/receipts/key → ${resp.status}`);
  const body = (await resp.json()) as Record<string, unknown>;
  const pem = (body.public_key_pem as string) ?? ((body.data as Record<string, unknown> | undefined)?.public_key_pem as string);
  if (!pem) throw new ArchiveError("public key response missing public_key_pem");
  writeFileSync(filePath, pem);
}

async function saveReceipt(
  fetchFn: typeof fetch,
  baseUrl: string,
  headers: Record<string, string>,
  execId: string,
  filePath: string
): Promise<void> {
  const resp = await fetchFn(`${baseUrl}/v1/receipts/${encodeURIComponent(execId)}`, { headers });
  if (resp.status === 401 || resp.status === 403)
    throw new ArchiveError(`/v1/receipts/${execId}: authentication failed (${resp.status})`);
  if (resp.status >= 500)
    throw new ArchiveError(`/v1/receipts/${execId}: server error ${resp.status}`);
  if (resp.status !== 200) throw new ArchiveError(`/v1/receipts/${execId} → ${resp.status}`);
  const body = (await resp.json()) as Record<string, unknown>;
  const payload = (body.data as unknown) ?? body;
  writeFileSync(filePath, JSON.stringify(payload));
}

async function saveInclusion(
  fetchFn: typeof fetch,
  baseUrl: string,
  headers: Record<string, string>,
  execId: string,
  filePath: string
): Promise<boolean> {
  const resp = await fetchFn(`${baseUrl}/v1/transparency/inclusion/${encodeURIComponent(execId)}`, { headers });
  if (resp.status === 404) return false;
  if (resp.status === 401 || resp.status === 403)
    throw new ArchiveError(`/v1/transparency/inclusion/${execId}: authentication failed (${resp.status})`);
  if (resp.status >= 500)
    throw new ArchiveError(`/v1/transparency/inclusion/${execId}: server error ${resp.status}`);
  if (resp.status !== 200) throw new ArchiveError(`/v1/transparency/inclusion/${execId} → ${resp.status}`);
  const body = (await resp.json()) as Record<string, unknown>;
  const payload = ((body.data as Record<string, unknown> | undefined) ?? body) as Record<string, unknown>;
  if (payload.pending === true) return false;
  writeFileSync(filePath, JSON.stringify(payload));
  return true;
}

async function saveRoot(
  fetchFn: typeof fetch,
  baseUrl: string,
  headers: Record<string, string>,
  rootDate: string,
  filePath: string
): Promise<boolean> {
  const resp = await fetchFn(`${baseUrl}/v1/transparency/roots/${encodeURIComponent(rootDate)}`, { headers });
  if (resp.status === 404) return false;
  if (resp.status === 401 || resp.status === 403)
    throw new ArchiveError(`/v1/transparency/roots/${rootDate}: authentication failed (${resp.status})`);
  if (resp.status >= 500)
    throw new ArchiveError(`/v1/transparency/roots/${rootDate}: server error ${resp.status}`);
  if (resp.status !== 200) throw new ArchiveError(`/v1/transparency/roots/${rootDate} → ${resp.status}`);
  const body = (await resp.json()) as Record<string, unknown>;
  const payload = (body.data as unknown) ?? body;
  writeFileSync(filePath, JSON.stringify(payload));
  return true;
}
