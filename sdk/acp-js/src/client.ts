import { ACPError, DeniedError, RateLimitedError } from "./errors.js";

export interface ClientOptions {
  apiKey?: string;
  baseUrl?: string;
  timeoutMs?: number;
  fetchImpl?: typeof fetch;
}

export interface ExecuteResult {
  decision: "allow" | "deny" | string;
  decision_id?: string;
  [k: string]: unknown;
}

export interface ReplayResult {
  execution_id: string;
  steps: unknown[];
  [k: string]: unknown;
}

/**
 * Thin client over the ACP gateway.
 *
 * Five-line integration:
 *
 *   const acp = new Client({ apiKey: "...", baseUrl: "https://acp.example.com" });
 *   const protectedAgent = acp.protect({ agentId: "agent_42" }, myAgent);
 *   await protectedAgent(prompt);
 */
export class Client {
  private readonly apiKey: string;
  private readonly baseUrl: string;
  private readonly timeoutMs: number;
  private readonly fetchImpl: typeof fetch;

  constructor(opts: ClientOptions = {}) {
    const apiKey = opts.apiKey ?? process.env.ACP_API_KEY;
    if (!apiKey) {
      throw new ACPError("ACP_API_KEY missing — pass apiKey or set the env var");
    }
    this.apiKey = apiKey;
    this.baseUrl = (opts.baseUrl ?? process.env.ACP_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");
    this.timeoutMs = opts.timeoutMs ?? 10_000;
    this.fetchImpl = opts.fetchImpl ?? fetch;
  }

  /**
   * Wrap an async agent function so every call goes through ACP.
   * Policy check before execution; audit + receipt after.
   */
  protect<Args extends unknown[], R>(
    { agentId, tool }: { agentId: string; tool?: string },
    fn: (...args: Args) => R | Promise<R>
  ): (...args: Args) => Promise<R> {
    const inferredTool = tool ?? fn.name ?? "unknown";
    return async (...args: Args): Promise<R> => {
      await this.execute({
        agentId,
        tool: inferredTool,
        payload: { args },
      });
      return await fn(...args);
    };
  }

  async execute({
    agentId,
    tool,
    payload,
  }: {
    agentId: string;
    tool: string;
    payload: Record<string, unknown>;
  }): Promise<ExecuteResult> {
    return await this._request("POST", "/execute", {
      body: { tool, payload },
      extraHeaders: { "X-Agent-ID": agentId, "X-ACP-Tool": tool },
    });
  }

  async replay(executionId: string): Promise<ReplayResult> {
    return await this._request("GET", `/flight/timeline/${encodeURIComponent(executionId)}`);
  }

  async verifyAudit(): Promise<Record<string, unknown>> {
    return await this._request("GET", "/audit/logs/verify");
  }

  async getReceipt(executionId: string): Promise<Record<string, unknown>> {
    return await this._request("GET", `/receipts/${encodeURIComponent(executionId)}`);
  }

  async publicKey(): Promise<Record<string, unknown>> {
    return await this._request("GET", "/receipts/key");
  }

  // ── Transparency log ────────────────────────────────────────────────────
  async listTransparencyRoots(opts: { since?: string; until?: string; limit?: number } = {}): Promise<Record<string, unknown>> {
    const q = new URLSearchParams();
    if (opts.since) q.set("since", opts.since);
    if (opts.until) q.set("until", opts.until);
    if (opts.limit !== undefined) q.set("limit", String(opts.limit));
    const qs = q.toString();
    return await this._request("GET", `/transparency/roots${qs ? `?${qs}` : ""}`);
  }

  async getTransparencyRoot(rootDate: string): Promise<Record<string, unknown>> {
    return await this._request("GET", `/transparency/roots/${encodeURIComponent(rootDate)}`);
  }

  async getInclusionProof(executionId: string): Promise<Record<string, unknown>> {
    return await this._request("GET", `/transparency/inclusion/${encodeURIComponent(executionId)}`);
  }

  async policySimulate(input: {
    agentId: string;
    tool: string;
    payload: Record<string, unknown>;
  }): Promise<Record<string, unknown>> {
    return await this._request("POST", "/policy/simulate", { body: input });
  }

  // ── plumbing ──────────────────────────────────────────────────────────
  private async _request<T = Record<string, unknown>>(
    method: string,
    path: string,
    opts: { body?: unknown; extraHeaders?: Record<string, string> } = {}
  ): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const res = await this.fetchImpl(`${this.baseUrl}${path}`, {
        method,
        signal: controller.signal,
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${this.apiKey}`,
          "User-Agent": "acp-js/0.1",
          ...(opts.extraHeaders ?? {}),
        },
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      });

      if (res.status === 403) {
        const body = await this._safeJson(res);
        throw new DeniedError(
          (body.reason as string) ?? "denied",
          (body.detail as string) ?? "policy denied this action",
          body.decision_id as string | undefined
        );
      }
      if (res.status === 429) {
        const retry = res.headers.get("retry-after");
        throw new RateLimitedError(retry ? Number(retry) : undefined);
      }
      if (!res.ok) {
        const text = await res.text();
        throw new ACPError(`${method} ${path} → ${res.status}: ${text.slice(0, 300)}`);
      }
      return (await this._safeJson(res)) as T;
    } finally {
      clearTimeout(timer);
    }
  }

  private async _safeJson(res: Response): Promise<Record<string, unknown>> {
    try {
      return (await res.json()) as Record<string, unknown>;
    } catch {
      return {};
    }
  }
}
