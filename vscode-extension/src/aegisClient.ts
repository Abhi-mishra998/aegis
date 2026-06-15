// Sprint 8 — Aegis HTTP client used by the VS Code extension.
//
// Authenticates with a long-lived Aegis API key (stored in VS Code's
// SecretStorage — never in settings.json). All requests go through the
// gateway, so the same multi-tenant guardrails as the production traffic
// path apply: the key resolves a tenant, every downstream call carries
// `X-Tenant-ID` derived from that key.

export interface ValidatedKey {
  tenantId: string;
  keyId: string;
}

export interface DecisionRow {
  audit_id: string;
  timestamp: string | null;
  agent_id: string | null;
  tool: string | null;
  action: string | null;
  decision: string | null;
  reason: string | null;
  risk_score: number | null;
  request_id: string | null;
}

export interface ReceiptEnvelope {
  execution_id?: string;
  tenant_id?: string;
  agent_id?: string;
  tool?: string;
  decision?: string;
  signed_at?: string;
  kid?: string;
  signature?: string;
  canonical_payload?: unknown;
  [extra: string]: unknown;
}

export class AegisClient {
  private gatewayUrl: string;
  private apiKey: string;
  private tenantId: string | null = null;

  constructor(gatewayUrl: string, apiKey: string) {
    this.gatewayUrl = gatewayUrl.replace(/\/$/, "");
    this.apiKey = apiKey;
  }

  async validateKey(): Promise<ValidatedKey> {
    const resp = await this.request("POST", "/api-keys/validate", {
      api_key: this.apiKey,
    });
    if (!resp.ok) {
      throw new Error(
        `Aegis API key rejected: HTTP ${resp.status} — ${await resp.text()}`,
      );
    }
    const body = (await resp.json()) as Record<string, unknown>;
    const data = (body?.["data"] ?? body) as Record<string, unknown>;
    const tenantId = data?.["tenant_id"];
    if (typeof tenantId !== "string") {
      throw new Error("Aegis key validated but no tenant_id returned.");
    }
    this.tenantId = tenantId;
    return { tenantId, keyId: String(data?.["id"] ?? "") };
  }

  async listRecentDecisions(limit = 25): Promise<DecisionRow[]> {
    this.requireTenant();
    const url = `/audit/logs?action=execute_tool&limit=${limit}&offset=0`;
    const resp = await this.request("GET", url);
    if (!resp.ok) {
      throw new Error(
        `audit/logs failed: HTTP ${resp.status} — ${await resp.text()}`,
      );
    }
    const body = (await resp.json()) as Record<string, unknown>;
    const outer = (body?.["data"] ?? body) as Record<string, unknown>;
    const items =
      (outer?.["items"] as unknown) ?? (body?.["items"] as unknown) ?? outer;
    return Array.isArray(items) ? (items as DecisionRow[]) : [];
  }

  async getReceipt(executionId: string): Promise<ReceiptEnvelope> {
    this.requireTenant();
    const resp = await this.request(
      "GET",
      `/receipts/${encodeURIComponent(executionId)}`,
    );
    if (!resp.ok) {
      throw new Error(
        `receipts/${executionId} failed: HTTP ${resp.status} — ${await resp.text()}`,
      );
    }
    const body = (await resp.json()) as Record<string, unknown>;
    return (body?.["data"] ?? body) as ReceiptEnvelope;
  }

  // Pure helper used by the smoke test to assert URL composition without
  // hitting a real network. Keeps URL-building logic out of the request
  // wrapper for testability.
  buildUrl(path: string): string {
    return `${this.gatewayUrl}${path.startsWith("/") ? path : "/" + path}`;
  }

  buildHeaders(extra?: Record<string, string>): Record<string, string> {
    return {
      "X-API-Key": this.apiKey,
      ...(this.tenantId ? { "X-Tenant-ID": this.tenantId } : {}),
      "Content-Type": "application/json",
      ...(extra ?? {}),
    };
  }

  private requireTenant(): void {
    if (!this.tenantId) {
      throw new Error(
        "AegisClient: validateKey() must be called before any other request.",
      );
    }
  }

  private async request(
    method: "GET" | "POST",
    path: string,
    body?: unknown,
  ): Promise<Response> {
    return fetch(this.buildUrl(path), {
      method,
      headers: this.buildHeaders(),
      body: body ? JSON.stringify(body) : undefined,
    });
  }
}
