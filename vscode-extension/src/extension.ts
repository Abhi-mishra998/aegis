// Sprint 8 — Aegis VS Code extension entry point.
//
// Activates on first use of the "Aegis Decisions" tree view. The API
// key is stored in VS Code SecretStorage (never settings.json). When the
// key is missing the tree shows a single "Set API key to begin" node.

import * as vscode from "vscode";
import { AegisClient, ReceiptEnvelope } from "./aegisClient";
import { AegisDecisionsProvider } from "./decisionsProvider";

const SECRET_KEY = "aegis.apiKey";

let pollTimer: NodeJS.Timeout | null = null;

export async function activate(
  context: vscode.ExtensionContext,
): Promise<void> {
  const provider = new AegisDecisionsProvider(() =>
    buildClient(context.secrets),
  );
  const view = vscode.window.createTreeView("aegisDecisions", {
    treeDataProvider: provider,
    showCollapseAll: false,
  });
  context.subscriptions.push(view);

  // Eager-load on activation so the user sees decisions immediately —
  // and so the SecretStorage prompt fires on first use rather than later.
  void provider.load();

  context.subscriptions.push(
    vscode.commands.registerCommand("aegis.refresh", () => provider.refresh()),
    vscode.commands.registerCommand("aegis.setApiKey", () =>
      promptApiKey(context, provider),
    ),
    vscode.commands.registerCommand("aegis.clearApiKey", () =>
      clearApiKey(context, provider),
    ),
    vscode.commands.registerCommand("aegis.openDecision", (row) =>
      openDecisionDetail(context, row),
    ),
  );

  schedulePoll(provider);
  context.subscriptions.push({
    dispose: () => {
      if (pollTimer) {
        clearTimeout(pollTimer);
        pollTimer = null;
      }
    },
  });

  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (
        event.affectsConfiguration("aegis.refreshIntervalSeconds") ||
        event.affectsConfiguration("aegis.gatewayUrl")
      ) {
        schedulePoll(provider);
      }
    }),
  );
}

export function deactivate(): void {
  if (pollTimer) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
}

async function buildClient(
  secrets: vscode.SecretStorage,
): Promise<AegisClient | null> {
  const apiKey = await secrets.get(SECRET_KEY);
  if (!apiKey) {
    return null;
  }
  const cfg = vscode.workspace.getConfiguration("aegis");
  const gateway = cfg.get<string>("gatewayUrl", "https://dev.aegisagent.in");
  const client = new AegisClient(gateway, apiKey);
  await client.validateKey();
  return client;
}

async function promptApiKey(
  context: vscode.ExtensionContext,
  provider: AegisDecisionsProvider,
): Promise<void> {
  const value = await vscode.window.showInputBox({
    prompt:
      "Paste your Aegis API key (POST /api-keys on the gateway). Stored in VS Code SecretStorage.",
    password: true,
    ignoreFocusOut: true,
    placeHolder: "aegis_…",
  });
  if (!value) {
    return;
  }
  await context.secrets.store(SECRET_KEY, value.trim());
  vscode.window.showInformationMessage(
    "Aegis: API key saved. Loading decisions…",
  );
  void provider.load();
}

async function clearApiKey(
  context: vscode.ExtensionContext,
  provider: AegisDecisionsProvider,
): Promise<void> {
  await context.secrets.delete(SECRET_KEY);
  provider.setNoKey();
  vscode.window.showInformationMessage("Aegis: API key cleared.");
}

function schedulePoll(provider: AegisDecisionsProvider): void {
  if (pollTimer) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
  const seconds = vscode.workspace
    .getConfiguration("aegis")
    .get<number>("refreshIntervalSeconds", 30);
  const ms = Math.max(5_000, Math.min(600_000, Math.round(seconds * 1000)));
  pollTimer = setInterval(() => provider.refresh(), ms);
}

async function openDecisionDetail(
  context: vscode.ExtensionContext,
  row: { audit_id: string; request_id?: string } | undefined,
): Promise<void> {
  if (!row) {
    return;
  }
  const panel = vscode.window.createWebviewPanel(
    "aegisDecisionDetail",
    `Aegis · ${row.audit_id?.slice(0, 8) ?? "decision"}`,
    vscode.ViewColumn.Beside,
    { enableScripts: false },
  );
  panel.webview.html = renderLoadingHtml();

  const client = await buildClient(context.secrets);
  if (!client) {
    panel.webview.html = renderErrorHtml(
      "Set the Aegis API key (command palette → “Aegis: Set API Key”) to load the signed receipt.",
    );
    return;
  }
  let receipt: ReceiptEnvelope | null = null;
  try {
    receipt = await client.getReceipt(row.audit_id);
  } catch (err) {
    panel.webview.html = renderErrorHtml(
      err instanceof Error ? err.message : String(err),
    );
    return;
  }
  panel.webview.html = renderReceiptHtml(receipt);
}

function renderLoadingHtml(): string {
  return `<!DOCTYPE html><html><body style="font-family: var(--vscode-font-family); padding: 1rem;">
    <h2>Loading receipt…</h2>
  </body></html>`;
}

function renderErrorHtml(msg: string): string {
  return `<!DOCTYPE html><html><body style="font-family: var(--vscode-font-family); padding: 1rem;">
    <h2 style="color:#f87171;">Could not load receipt</h2>
    <pre>${escapeHtml(msg)}</pre>
  </body></html>`;
}

function renderReceiptHtml(r: ReceiptEnvelope): string {
  const raw = JSON.stringify(r, null, 2);
  const fields: [string, string][] = [
    ["execution_id", String(r.execution_id ?? "")],
    ["tenant_id", String(r.tenant_id ?? "")],
    ["agent_id", String(r.agent_id ?? "")],
    ["tool", String(r.tool ?? "")],
    ["decision", String(r.decision ?? "")],
    ["signed_at", String(r.signed_at ?? "")],
    ["kid (signing key)", String(r.kid ?? "")],
    ["signature", String(r.signature ?? "")],
  ];
  const rows = fields
    .map(
      ([k, v]) =>
        `<tr><th style="text-align:left;padding-right:1rem;color:#9ca3af;font-weight:500;">${escapeHtml(k)}</th><td style="font-family:var(--vscode-editor-font-family);">${escapeHtml(v)}</td></tr>`,
    )
    .join("");
  return `<!DOCTYPE html><html><body style="font-family: var(--vscode-font-family); padding: 1rem;">
    <h2>Aegis signed receipt</h2>
    <table>${rows}</table>
    <h3 style="margin-top:1.5rem;">Raw envelope</h3>
    <pre style="background:#0a0a0a;padding:0.75rem;border-radius:4px;color:#d4d4d8;overflow-x:auto;">${escapeHtml(raw)}</pre>
  </body></html>`;
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
