import { emitAuthFailure } from "../lib/authEvents";
import { parseRule, parseRuleList } from "../lib/schemas";
import { attachClerkAuth, getFreshClerkToken, hasClerkAuth } from "./clerkAuth";

// In dev: empty string → relative URLs → Vite proxy routes to :8000 (same-origin, no CORS/cookie issues).
// In production (Docker/k8s): set VITE_GATEWAY_URL=https://your-gateway or leave empty for nginx proxy.
const API_BASE = import.meta.env.VITE_GATEWAY_URL || "";

/**
 * ACP API Client
 *
 * C-6/UI-9 FIX: Token is NO LONGER stored in localStorage.
 * Authentication is handled exclusively via the httpOnly `acp_token` cookie
 * set by the Gateway on login. This eliminates the XSS token-theft vector.
 *
 * Session metadata (tenant_id, expiry, user_email) is stored in localStorage
 * as non-sensitive identifiers only — not the token itself.
 */

export const setSessionMetadata = (data) => {
  if (data.tenant_id) localStorage.setItem("tenant_id", data.tenant_id);
  if (data.user_email) localStorage.setItem("user_email", data.user_email);
  if (data.role) localStorage.setItem("user_role", String(data.role).toUpperCase());
  if (data.agent_id) localStorage.setItem("agent_id", data.agent_id);
  if (data.expires_in) {
    localStorage.setItem("acp_token_expiry", String(Date.now() + data.expires_in * 1000));
  }
};

export const clearSessionMetadata = () => {
  localStorage.removeItem("tenant_id");
  localStorage.removeItem("user_email");
  localStorage.removeItem("acp_token_expiry");
  localStorage.removeItem("user_role");
  localStorage.removeItem("agent_id");
  localStorage.removeItem("sse_query_token");
};

// Module-local: only the request() / blobRequest() helpers consume this.
// External callers should rely on the response of those helpers (or watch
// the AUTH_EVENTS.FAILURE event from authEvents.js) instead of mirroring
// the gate logic themselves.
const isSessionValid = () => {
  // Mirror App.jsx readSessionState: require tenant_id AND a non-expired expiry.
  // The httpOnly cookie remains the server-side source of truth, but client-side
  // gating must match the App-level redirect predicate or we leak requests with
  // an expired session before the auth event clears state.
  //
  // Clerk path: if ClerkAuthBridge has registered a token getter, an active
  // Clerk session exists — the gateway will validate the Bearer JWT directly.
  // Accept that as session-valid even before the bridge has mirrored
  // tenant_id into localStorage.
  if (hasClerkAuth()) return true;
  const tenantId = localStorage.getItem("tenant_id");
  const expiry = parseInt(localStorage.getItem("acp_token_expiry") || "0", 10);
  return !!tenantId && expiry > Date.now();
};

// Identity endpoints whose response IS the authoritative answer about
// "who am I / what workspace am I in" — for these, a tenant_id that
// differs from the cached localStorage value means the cache is stale
// (Clerk org switch, multi-tenant user, post-provision refresh), NOT a
// cross-tenant leak. We reconcile localStorage from the response.
// Every OTHER endpoint scoped to a tenant is treated as a real leak
// check: if the gateway accidentally returns another tenant's data, kill
// the session.
const _IDENTITY_PATHS = new Set([
  "/auth/me",
  "/workspace/me",
]);

const _isIdentityPath = (url) => {
  if (typeof url !== "string") return false;
  // Strip query string + leading host (request() accepts either /path or
  // a full URL with overrideBase).
  const path = url.split("?")[0].replace(/^https?:\/\/[^/]+/, "");
  return _IDENTITY_PATHS.has(path);
};

// Shared status → JSON pipeline. Used by request() AND by the
// post-Clerk-refresh retry below so a fresh-token replay surfaces the
// success body (or downstream 4xx/5xx) through the exact same handling
// the caller already expects.
const _handleResponse = async (res, url) => {
  if (res.status === 429) {
    const txt = await res.text().catch(() => "");
    let msg = "Too many requests — please wait a moment and try again.";
    try { const p = JSON.parse(txt); msg = p.detail || p.error || msg; } catch {}
    console.warn(`RATE_LIMITED [429] ${url}:`, msg);
    const rlErr = new Error(msg);
    rlErr._noRetry = true;
    throw rlErr;
  }

  if (!res.ok) {
    const errorText = await res.text();
    let parsedError = errorText;
    try {
      const parsed = JSON.parse(errorText);
      parsedError = parsed.error || parsed.detail || parsed.message || errorText;
    } catch (e) {
      // Non-JSON body. If it looks like an HTML error page (nginx default,
      // load-balancer block, WAF rejection), collapse it to a short string so
      // downstream renders ("Decision: <HTML>…") don't leak raw markup.
      if (typeof errorText === "string" && /^\s*<(!doctype|html|head|body|center)/i.test(errorText)) {
        parsedError = `Upstream returned HTML ${res.status}`;
      }
    }
    console.error(`API_ERROR [${res.status}] ${url}:`, parsedError);
    const apiErr = new Error(parsedError || "API Error");
    apiErr._status = res.status;
    if (res.status >= 400 && res.status < 500) apiErr._noRetry = true;
    throw apiErr;
  }

  const text = await res.text();
  const json = text ? JSON.parse(text) : {};

  const sessionTenant = localStorage.getItem("tenant_id");
  const responseTenant = json?.data?.tenant_id ?? json?.tenant_id;
  if (sessionTenant && responseTenant && responseTenant !== sessionTenant) {
    if (_isIdentityPath(url)) {
      // The response is authoritative for our own identity. Reconcile
      // the cache silently — this happens whenever a user's Clerk
      // session swaps active orgs (multi-tenant users), or right after
      // /auth/clerk/provision rewrites User.tenant_id to match the
      // currently-active workspace. Killing the session here is the
      // single biggest source of "Authentication boundary violated"
      // overlay flashes during normal usage.
      console.info("TENANT_RECONCILE: updating cached tenant_id from /auth/me-class response", {
        sessionTenant, responseTenant, url,
      });
      try { localStorage.setItem("tenant_id", responseTenant); } catch (_) {}
    } else {
      console.error("TENANT_MISMATCH: response tenant differs from session", { responseTenant, sessionTenant });
      emitAuthFailure({ reason: "tenant_mismatch", url, statusCode: 403 });
      throw new Error("TENANT_MISMATCH: Cross-tenant data rejected");
    }
  }

  return json;
};

const request = async (url, options = {}, retry = 1) => {
  try {
    const tenantId = localStorage.getItem("tenant_id");

    // AUTH GATE: Block requests (except auth/health) if session is expired or missing.
    // /auth/sso/providers is public — Login page fetches it before any token
    // exists, so it must be exempt or the gate throws before we even reach login.
    const isAuthPath =
      url.includes("/auth/token") ||
      url.includes("/auth/sso/providers") ||
      url.includes("/health");

    if (!isSessionValid() && !isAuthPath) {
      console.warn("API_GATED: Session expired or not found.", url);
      emitAuthFailure({ reason: "session_expired", url });
      throw new Error("UNAUTHENTICATED: Session expired.");
    }

    const headers = {
      "Content-Type": "application/json",
      ...(tenantId && { "X-Tenant-ID": tenantId }),
      "X-Request-ID": crypto.randomUUID(),
      "X-Timestamp": Date.now().toString(),
      ...(options.headers || {}),
    };

    // Attach Clerk Bearer token if a session exists. Falls through silently
    // (no header) for legacy cookie-auth flows so old admin@acp.local users
    // still work. Gateway accepts either when ACP_AUTH_PROVIDER=both.
    await attachClerkAuth(headers);

    const base = options.overrideBase || API_BASE;
    const finalUrl = url.startsWith("http") ? url : `${base}${url}`;

    const res = await fetch(finalUrl, {
      ...options,
      headers,
      credentials: "include", // Always send httpOnly cookies
    });

    if (res.status === 401) {
      // Clerk session race: the Clerk JWT lifetime is 60s. Even with the
      // exp-aware preflight in attachClerkAuth, requests can be in flight
      // when the SDK rotates underneath them. Before declaring the session
      // dead and bouncing the user to /login, ask Clerk to skip its
      // in-memory cache, fetch a fresh JWT, and replay ONCE. If THAT also
      // 401s, the session really is dead.
      const isClerkSession = hasClerkAuth();
      if (isClerkSession && !options._authRetried) {
        try {
          const freshToken = await getFreshClerkToken();
          if (freshToken) {
            const retryHeaders = {
              ...headers,
              Authorization: `Bearer ${freshToken}`,
            };
            const retryRes = await fetch(finalUrl, {
              ...options,
              headers: retryHeaders,
              credentials: "include",
            });
            if (retryRes.status !== 401) {
              // Quiet success — the first 401 is a Clerk-rotation artifact,
              // not a real auth failure; the user's network panel still
              // shows it, but the application path is OK so no console
              // noise here.
              return await _handleResponse(retryRes, url);
            }
          }
        } catch (refreshErr) {
          console.warn("Clerk refresh-on-401 failed", refreshErr);
        }
      }
      // Only log + emit auth_failure when the retry path also failed (or
      // wasn't applicable). Logging on the first 401 of every Clerk-token-
      // rotation moment flooded the console; users mistook the resolved
      // requests for a permanent outage.
      console.error(`AUTHENTICATION_REQUIRED [401] ${url}`);
      clearSessionMetadata();
      if (window.location.pathname !== "/login") {
        emitAuthFailure({ reason: "unauthorized", url, statusCode: 401 });
      }
      const authErr = new Error("UNAUTHORIZED: Session expired or credentials invalid.");
      authErr._noRetry = true;
      throw authErr;
    }

    return await _handleResponse(res, url);
  } catch (err) {
    if (err.name === "TypeError" && err.message === "Failed to fetch") {
      console.error(`NETWORK_ERROR: Cannot reach ${API_BASE}. Check backend services.`);
    } else {
      console.error(`REQUEST_FAILED ${url}:`, err);
    }
    // Only retry on transient network errors — never on 4xx or auth failures
    if (retry > 0 && !err._noRetry && err.name === "TypeError") {
      return request(url, options, retry - 1);
    }
    throw err;
  }
};


/**
 * Like request() but returns a Blob (for PDF downloads).
 * Uses credentials:'include' — no token in localStorage or headers.
 */
const blobRequest = async (url, options = {}) => {
  if (!isSessionValid()) {
    emitAuthFailure({ reason: "session_expired", url });
    throw new Error("UNAUTHENTICATED: Session expired.");
  }
  const tenantId = localStorage.getItem("tenant_id");
  const headers = {
    ...(options.headers || {}),
    ...(tenantId && { "X-Tenant-ID": tenantId }),
    "X-Request-ID": crypto.randomUUID(),
  };
  await attachClerkAuth(headers);
  const finalUrl = url.startsWith("http") ? url : `${API_BASE}${url}`;
  const res = await fetch(finalUrl, { ...options, headers, credentials: "include" });
  if (res.status === 401) {
    clearSessionMetadata();
    emitAuthFailure({ reason: "unauthorized", url, statusCode: 401 });
    throw new Error("UNAUTHORIZED: Session expired.");
  }
  if (!res.ok) throw new Error(`Request failed: ${res.status}`);
  return res.blob();
};


// Auth Service
export const authService = {
  login: async (data) => {
    console.info("AUTHENTICATION_ATTEMPT", { email: data.email });

    const headers = { "Content-Type": "application/json" };
    // Always send X-Tenant-ID. Browser login never knows the tenant upfront,
    // so fall back to the default system tenant. The identity service rejects
    // mismatched tenants (401) if the user belongs to a different one.
    const tenantIdInput = data.tenant_id || data.tenantId || "00000000-0000-0000-0000-000000000001";
    headers["X-Tenant-ID"] = tenantIdInput;

    const res = await fetch(`${API_BASE}/auth/token`, {
      method: "POST",
      headers,
      credentials: "include",
      body: JSON.stringify({ email: data.email, password: data.password }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || "Login failed");
    }

    const json = await res.json();

    // Token goes into the httpOnly cookie (set by the gateway) — NOT localStorage
    const tenantId = json?.tenant_id || json?.data?.tenant_id;
    const expiresIn = json?.expires_in || json?.data?.expires_in || 900;
    const role = json?.role || json?.data?.role;

    // SSE auth is cookie-only (httpOnly acp_token set by the gateway). Query-
    // string fallback was retired in gateway sprint-1 because the token leaks
    // via nginx/ALB access logs, browser history, and Referer headers. Clear
    // any leftover value so useSSE doesn't append a ?token= that the gateway
    // would now reject with 401.
    try { localStorage.removeItem("sse_query_token"); } catch (_) {}

    setSessionMetadata({ tenant_id: tenantId, expires_in: expiresIn, role });

    console.info("AUTHENTICATION_SUCCESS", { tenantId, role });

    return {
      data: {
        tenant_id: tenantId,
        expires_in: expiresIn,
        role,
        user: json?.user || json?.data?.user,
      },
    };
  },

  logout: async () => {
    try {
      await fetch(`${API_BASE}/auth/logout`, { method: "POST", credentials: "include" });
    } finally {
      clearSessionMetadata();
    }
  },

  getMe: () => request("/auth/me"),
};

// Common API object — only methods actually called by components live here.
// All other operations use their service-specific exports below.
export const api = {
  ...authService,
  getApiKeys: () => request("/api-keys"),
  createApiKey: (data) => request("/api-keys", { method: "POST", body: JSON.stringify(data) }),
  revokeApiKey: (id) => request(`/api-keys/${id}`, { method: "DELETE" }),
  getBilling: () => request("/billing/summary"),
  getRisk: () => request("/risk/summary"),
  getSSOProviders: () => request("/auth/sso/providers").catch(() => ({ providers: [] })),
};

// Sprint 3-8 — Workspace surface (shadow mode + inventory + system values).
export const workspaceService = {
  me: () => request("/workspace/me"),
  exitShadowMode: () =>
    request("/workspace/exit-shadow-mode", { method: "POST" }),
  inventory: () => request("/workspace/inventory"),
  // Sprint 8 — OWNER-only. Merge dollar weights per resource kind. Sending
  // a key with value 0 removes that kind from the map.
  updateSystemValues: (values) =>
    request("/workspace/system-values", {
      method: "PATCH",
      body: JSON.stringify(values || {}),
    }),
  // Sprint 21 — Slack approvals config. The GET surface returns
  // {webhook_url, configured} (the signing secret is stripped by the
  // gateway). The PUT body is {webhook_url, rotate_secret?}.
  getSlackConfig: () => request("/workspace/slack-config"),
  setSlackConfig: (body) =>
    request("/workspace/slack-config", {
      method: "PUT",
      body: JSON.stringify(body || {}),
    }),

  // Sprint 23 — Compliance Policy Packs.
  policyPacksCatalog: () => request("/policy-packs/catalog"),
  getPolicyPacks: () => request("/workspace/policy-packs"),
  setPolicyPacks: (body) =>
    request("/workspace/policy-packs", {
      method: "PUT",
      body: JSON.stringify(body || {}),
    }),
};


// Sprint 5-7 — Identity & Access Graph + Blast Radius read API.
// Surfaces the IAG endpoints that were built but never wired to the UI,
// plus the Sprint 7 MITRE coverage grid that pulls from signal_registry.
export const iagService = {
  getAgent: (agentId) => request(`/iag/agents/${encodeURIComponent(agentId)}`),
  getBlastRadius: (incidentId) =>
    request(`/iag/incidents/${encodeURIComponent(incidentId)}/blast-radius`),
  getMitreCoverage: () => request("/iag/mitre-coverage"),
};

// Sprint 5 — Auto-Remediation. Read policy + ledger; force replay on demand.
export const remediationService = {
  getPolicy: () => request("/remediation/policy"),
  getLedger: (incidentId) =>
    request(`/remediation/incidents/${encodeURIComponent(incidentId)}`),
  replay: (incidentId) =>
    request(`/remediation/incidents/${encodeURIComponent(incidentId)}/replay`, {
      method: "POST",
    }),
  dryRun: (body) =>
    request("/remediation/dry-run", {
      method: "POST",
      body: JSON.stringify(body || {}),
    }),
};

// Helper to append agent_id to existing URLs.
const _withAgent = (url, agentId) => {
  if (!agentId) return url;
  return url + (url.includes("?") ? "&" : "?") + `agent_id=${encodeURIComponent(agentId)}`;
};

export const auditService = {
  getSummary: (agentId) => request(_withAgent("/audit/logs/summary", agentId)),
  getLogs: (limit = 10, offset = 0, agentId) =>
    request(_withAgent(`/audit/logs?limit=${limit}&offset=${offset}`, agentId)),
  getAgentLogs: (agentId, limit = 15) => request(`/audit/logs?agent_id=${agentId}&limit=${limit}`),
  getKillSwitchHistory: (limit = 20) => request(`/audit/logs?action=kill&limit=${limit}`),
  // Sprint 3 — Shadow Mode review feed.
  getShadowEvents: (limit = 50, offset = 0, agentId) =>
    request(
      _withAgent(
        `/audit/logs?action=would_have_blocked&limit=${limit}&offset=${offset}`,
        agentId,
      ),
    ),
  searchLogs: (params) => {
    // AWS WAFv2 SQLi managed rule blocks any JSON body with `"limit":<n>` (it
    // reads `LIMIT N` as SQL injection). We route the same filters through
    // GET /audit/logs query params so the body never enters body inspection.
    const qs = new URLSearchParams()
    Object.entries(params || {}).forEach(([k, v]) => {
      if (v === undefined || v === null || v === '') return
      qs.append(k, String(v))
    })
    const q = qs.toString()
    return request(`/audit/logs${q ? `?${q}` : ''}`)
  },
  verifyIntegrity: () => request("/audit/logs/verify"),
  getHeatmap: () => request("/audit/logs/heatmap"),
  explainDecision: (auditId) => request(`/audit/logs/${encodeURIComponent(auditId)}/explain`),
  getDriftReport: (agentId, baselineDays = 7, comparisonHours = 24) =>
    request(`/audit/drift/${encodeURIComponent(agentId)}?baseline_days=${baselineDays}&comparison_hours=${comparisonHours}`),
  getRiskTrend: (agentId, days = 30) =>
    request(`/audit/risk-trend/${encodeURIComponent(agentId)}?days=${days}`),
  getToolBreakdown: (days = 30, limit = 20) =>
    request(`/audit/tool-breakdown?days=${days}&limit=${limit}`),
  getPeerBenchmark: (agentId, days = 30) =>
    request(`/audit/peer-benchmark/${encodeURIComponent(agentId)}?days=${days}`),
  getTopFindings: (days = 30, limit = 15, agentId) =>
    request(_withAgent(`/audit/top-findings?days=${days}&limit=${limit}`, agentId)),
  getAnomalyTrends: (days = 30) =>
    request(`/audit/trends?days=${days}`),
  getHourlyActivity: (days = 7) =>
    request(`/audit/hourly-activity?days=${days}`),
  getRiskHistogram: (days = 30, agentId) =>
    request(_withAgent(`/audit/risk-histogram?days=${days}`, agentId)),
  getWeeklyHeatmap: (days = 28) =>
    request(`/audit/weekly-heatmap?days=${days}`),
  getDecisionTrend: (days = 30) =>
    request(`/audit/decision-trend?days=${days}`),
  getAgentActivity: (limit = 20) =>
    request(`/audit/agent-activity?limit=${limit}`),
  getHighRiskEvents: (days = 7, limit = 20, threshold = 0.7, agentId) =>
    request(_withAgent(`/audit/high-risk-events?days=${days}&limit=${limit}&threshold=${threshold}`, agentId)),
  getDenyReasons: (days = 30, limit = 15) =>
    request(`/audit/deny-reasons?days=${days}&limit=${limit}`),
  getAgentToolUsage: (agentId, days = 30) =>
    request(`/audit/tool-usage/${encodeURIComponent(agentId)}?days=${days}`),
  getToolRisk: (days = 30, limit = 20, agentId) =>
    request(_withAgent(`/audit/tool-risk?days=${days}&limit=${limit}`, agentId)),
  getRiskPercentileTrend: (days = 30, agentId) =>
    request(_withAgent(`/audit/risk-percentile-trend?days=${days}`, agentId)),
  getDailyActiveAgents: (days = 30) =>
    request(`/audit/daily-active-agents?days=${days}`),
  getFindingBreakdown: (days = 30, limit = 20) =>
    request(`/audit/finding-breakdown?days=${days}&limit=${limit}`),
  getAgentDailyDecisions: (agentId, days = 30) =>
    request(`/audit/agent-daily-decisions/${encodeURIComponent(agentId)}?days=${days}`),
  getAgentFindings: (agentId, days = 30) =>
    request(`/audit/agent-findings/${encodeURIComponent(agentId)}?days=${days}`),
  getPostureScoreTrend: (days = 30) =>
    request(`/audit/posture-score-trend?days=${days}`),
  getEscalationRateTrend: (days = 30) =>
    request(`/audit/escalation-rate-trend?days=${days}`),
  getNotes: (auditId) => request(`/audit/logs/${encodeURIComponent(auditId)}/notes`),
  addNote: (auditId, data) => request(`/audit/logs/${encodeURIComponent(auditId)}/notes`, {
    method: 'POST', body: JSON.stringify(data),
  }),
};

export const registryService = {
  listAgents: (params = {}) => {
    const query = new URLSearchParams(params).toString();
    return request(`/agents${query ? "?" + query : ""}`);
  },
  getTools: () => request("/registry/tools"),
  getAgent: (id) => request(`/agents/${id}`),
  createAgent: (data) => request("/agents", { method: "POST", body: JSON.stringify(data) }),
  updateAgent: (id, data) => request(`/agents/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  deleteAgent: (id) => request(`/agents/${id}`, { method: "DELETE" }),
  addPermission: (id, data) => request(`/agents/${id}/permissions`, { method: "POST", body: JSON.stringify(data) }),
  listPermissions: (id) => request(`/agents/${id}/permissions`),
  revokePermission: (agentId, permId) =>
    request(`/agents/${agentId}/permissions/${permId}`, { method: "DELETE" }),
  getProfile: (id) => request(`/agents/${id}/profile`),
  wizard: (data) =>
    request("/agents/wizard", { method: "POST", body: JSON.stringify(data) }),
  // Sprint 13 — capability catalog the wizard renders as a checkbox grid.
  wizardCapabilities: () => request("/agents/wizard/capabilities"),
  // Sprint 13 — live preview of the policies that will fire for a
  // capability selection. Called every time the operator ticks /
  // unticks a box.
  wizardPolicyPreview: (capabilities) =>
    request(
      "/agents/wizard/policy-preview?capabilities=" +
        encodeURIComponent((capabilities || []).join(",")),
    ),
  installSnippet: (agentId, provider, aegisApiKey) =>
    request(
      `/agents/wizard/install-snippet/${agentId}/${provider}` +
        (aegisApiKey ? `?aegis_api_key=${encodeURIComponent(aegisApiKey)}` : ""),
    ),
  getSummary: () => request("/agents/summary"),
};

export const riskService = {
  getSummary: (agentId) => request(_withAgent("/risk/summary", agentId)),
  getTimeline: (agentId) => request(_withAgent("/risk/timeline", agentId)),
  getTopThreats: (agentId) => request(_withAgent("/risk/top-threats", agentId)),
  getInsights: (agentId) => request(_withAgent("/insights/recent", agentId)),
  getSignalWeights: () => request("/risk/signal-weights"),
};

export const forensicsService = {
  listInvestigations: (params = {}) => {
    const q = new URLSearchParams();
    if (params.min_risk != null) q.set('min_risk', params.min_risk);
    if (params.limit)    q.set('limit',    params.limit);
    if (params.start_time) q.set('start_time', params.start_time);
    if (params.end_time)   q.set('end_time',   params.end_time);
    const qs = q.toString();
    return request(`/forensics/investigation${qs ? `?${qs}` : ''}`);
  },
  getInvestigation: (id, window_hours = 24) =>
    request(`/forensics/investigation/${id}?window_hours=${window_hours}`),
  getReplay:       (id, limit = 50) => request(`/forensics/replay/${id}?limit=${limit}`),
  getTimeline:     (id) => request(`/forensics/timeline/${id}`),
  getBlastRadius:  (id, depth = 3) => request(`/forensics/blast-radius/${id}?depth=${depth}`),
  exportInvestigation: (id) => request(`/forensics/export/${id}`, { method: 'POST' }),
};

export const billingService = {
  getSummary: (agentId) => request(_withAgent("/billing/summary", agentId)),
  getInvoices: (agentId) => request(_withAgent("/billing/invoices", agentId)),
  getDashboard: () => request("/usage/dashboard"),
  getAnomalies: () => request("/usage/anomalies"),
  listBudgetRequests: (status) => request(`/billing/budget-requests${status ? `?status=${status}` : ''}`),
  createBudgetRequest: (data) => request('/billing/budget-requests', { method: 'POST', body: JSON.stringify(data) }),
  approveBudgetRequest: (id, data) => request(`/billing/budget-requests/${id}/approve`, { method: 'POST', body: JSON.stringify(data) }),
  rejectBudgetRequest: (id, data) => request(`/billing/budget-requests/${id}/reject`, { method: 'POST', body: JSON.stringify(data) }),
  getCostAttribution: (weeks = 4, agentId) => request(_withAgent(`/billing/cost-attribution?weeks=${weeks}`, agentId)),
  // Sprint 9 — Stripe wiring.
  getPlan: () => request("/billing/plan"),
  createCheckoutSession: (tier) =>
    request("/billing/checkout-session", {
      method: "POST",
      body: JSON.stringify({ tier }),
    }),
  createPortalSession: (customerId) =>
    request("/billing/portal-session", {
      method: "POST",
      body: JSON.stringify({ customer_id: customerId }),
    }),
};

export const playgroundService = {
  execute: (agentId, tool, payload, options = {}) =>
    request("/execute", {
      method: "POST",
      body: JSON.stringify({ tool, payload }),
      ...options,
      headers: {
        "X-Agent-ID": agentId,
        "X-ACP-Tool":  tool,
        ...(options.headers || {}),
      },
    }),
};

export const decisionService = {
  getHistory: (limit = 20, agentId) => request(_withAgent(`/decision/history?limit=${limit}`, agentId)),
};

export const dashboardService = {
  getState: () => request('/dashboard/state'),
  getSystemHealth: () => request('/system/health'),
  // Sprint 12 — single fetch powering the post-login Dashboard hero
  // (6 mandate KPIs + 4 business-value KPIs). Backed by
  // services/gateway/routers/messages.py::dashboard_overview which
  // fans out to /workspace/inventory + audit-svc /logs.
  overview: () => request('/dashboard/overview'),
}

// Sprint 19 — approval resume API. The SDK + Approval Inbox both
// poll GET /approvals/{id}/status to see whether the operator has
// cleared a previously-escalated request. Returns the normalized
// {status, approver_role, matched_pattern, decided_at, decided_by,
// reason, prompt_excerpt} shape.
export const approvalService = {
  status: (id) => request(`/approvals/${encodeURIComponent(id)}/status`),
}

export const policyService = {
  simulate: (payload) => request("/policy/simulate", {
    method: "POST",
    body: JSON.stringify(payload),
  }),
  testPolicy: (payload) => request("/policy/test", { method: "POST", body: JSON.stringify(payload) }),
  uploadPolicy: (payload) => request("/policy/upload", { method: "POST", body: JSON.stringify(payload) }),
};

export const socService = {
  getTimeline: (limit = 60) => request(`/audit/logs/soc-timeline?limit=${limit}`),
};

export const incidentService = {
  getSummary: (agentId) => request(_withAgent("/incidents/summary", agentId)),
  getTransitions: () => request("/incidents/transitions"),
  list: (params = {}) => {
    const q = new URLSearchParams();
    if (params.status)   q.set("status",   params.status);
    if (params.severity) q.set("severity", params.severity);
    if (params.limit)    q.set("limit",    params.limit);
    if (params.offset)   q.set("offset",   params.offset);
    if (params.agentId)  q.set("agent_id", params.agentId);
    const qs = q.toString();
    return request(`/incidents${qs ? `?${qs}` : ""}`);
  },
  get: (id) => request(`/incidents/${id}`),
  update: (id, payload) => request(`/incidents/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  }),
  addAction: (id, payload) => request(`/incidents/${id}/actions`, {
    method: "POST",
    body: JSON.stringify(payload),
  }),
  getComments: (id) => request(`/incidents/${id}/comments`),
  addComment: (id, data) => request(`/incidents/${id}/comments`, {
    method: "POST",
    body: JSON.stringify(data),
  }),
  exportPdf: (id) => blobRequest(`/incidents/${id}/export`, { method: 'POST' }),
};

export const autoResponseService = {
  listRules: async () => {
    const res = await request('/auto-response/rules')
    if (res?.data) res.data = parseRuleList(res.data)
    return res
  },
  getRule: async (id) => {
    const res = await request(`/auto-response/rules/${id}`)
    if (res?.data) res.data = parseRule(res.data)
    return res
  },
  createRule: async (data) => {
    const res = await request('/auto-response/rules', { method: 'POST', body: JSON.stringify(data) })
    if (res?.data) res.data = parseRule(res.data)
    return res
  },
  updateRule: async (id, data) => {
    const res = await request(`/auto-response/rules/${id}`, { method: 'PATCH', body: JSON.stringify(data) })
    if (res?.data) res.data = parseRule(res.data)
    return res
  },
  deleteRule:     (id)           => request(`/auto-response/rules/${id}`, { method: 'DELETE' }),
  getStatus:      ()             => request('/auto-response/toggle'),
  toggle:         (enabled)      => request('/auto-response/toggle', { method: 'POST', body: JSON.stringify({ enabled }) }),
  simulate:       (data)         => request('/auto-response/simulate', { method: 'POST', body: JSON.stringify(data) }),
  getHistory:     (id)           => request(`/auto-response/rules/${id}/history`),
  rollback:       (id, version)  => request(`/auto-response/rules/${id}/rollback/${version}`, { method: 'POST' }),
  feedback:       (id, data)     => request(`/auto-response/rules/${id}/feedback`, { method: 'POST', body: JSON.stringify(data) }),
  getMetrics:     ()             => request('/auto-response/metrics'),
  listPending:    ()             => request('/auto-response/pending'),
  approvePending: (key, data)    => request(`/auto-response/pending/${key}/approve`, { method: 'POST', body: JSON.stringify(data) }),
  replay:         (data)         => request('/auto-response/replay', { method: 'POST', body: JSON.stringify(data) }),
  getLatency:     ()             => request('/auto-response/latency'),
};

// 2026-05-13: Runtime Trust Infrastructure
export const graphService = {
  listAgents:           (limit = 500)        => request(`/graph/agents?limit=${limit}`),
  getAgent:             (id)                 => request(`/graph/agent/${id}`),
  getBlastRadius:       (id, depth = 3)      => request(`/graph/blast-radius/${id}?depth=${depth}`),
  getRiskyPaths:        (limit = 50)         => request(`/graph/risky-paths?limit=${limit}`),
  getTrustBoundaries:   ()                   => request('/graph/trust-boundaries'),
  getRuntimeRelationships: (minutes = 60)    => request(`/graph/runtime-relationships?minutes=${minutes}`),
  getTrust:             (nodeId, limit = 100) => request(`/graph/trust/${nodeId}?limit=${limit}`),
  listDrift:            (minutes = 1440)     => request(`/graph/drift?minutes=${minutes}`),
  simulateCompromise:   (body)               => request('/graph/compromise/simulate', { method: 'POST', body: JSON.stringify(body) }),
};

// Sprint 4 — Fleet dashboard surface.
//   /audit/fleet/*  — decision-derived KPIs, time-series, agent health, recent
//   /usage/fleet/*  — per-tenant/per-agent inference-cost burn-down
//
// Every method here is tenant-scoped at the backend via the JWT claim; the
// client just forwards query params.
// Sprint 7 — Policy Playground.
//   /audit/playground/validate  — compile rules_json → Rego + OPA parse check
//   /audit/playground/replay    — replay candidate against historical audit_logs
//   /audit/playground/publish   — persist as a Sprint-6 ShadowPolicy
//
// Per-tenant scoped at the backend via the JWT claim. Named to disambiguate
// from the pre-existing `playgroundService` for the /execute tester.
export const policyPlaygroundService = {
  validate: (rules, policyName = 'aegis_policy') => request('/audit/playground/validate', {
    method: 'POST',
    body: JSON.stringify({ rules, policy_name: policyName }),
  }),
  replay:   (body) => request('/audit/playground/replay', {
    method: 'POST', body: JSON.stringify(body),
  }),
  publish:  (body) => request('/audit/playground/publish', {
    method: 'POST', body: JSON.stringify(body),
  }),
}

// Sprint 6 — Shadow-mode policies + online evaluation.
//   /audit/shadow/policies/*          — CRUD + promote/rollback
//   /audit/shadow/policies/{id}/would-have-denied  — drift report
//   /audit/shadow/online-eval         — per-tenant drift config
//
// Per-tenant scoped at the backend via the JWT claim.
export const shadowService = {
  listPolicies:    (params = {}) => {
    const q = new URLSearchParams()
    if (params.mode)     q.set('mode', params.mode)
    if (params.agent_id) q.set('agent_id', params.agent_id)
    return request(`/audit/shadow/policies${q.toString() ? `?${q.toString()}` : ''}`)
  },
  createPolicy:    (body) => request('/audit/shadow/policies', {
    method: 'POST', body: JSON.stringify(body),
  }),
  getPolicy:       (id) => request(`/audit/shadow/policies/${id}`),
  editPolicy:      (id, body) => request(`/audit/shadow/policies/${id}`, {
    method: 'PATCH', body: JSON.stringify(body),
  }),
  archivePolicy:   (id) => request(`/audit/shadow/policies/${id}`, { method: 'DELETE' }),
  promotePolicy:   (id, target) => request(`/audit/shadow/policies/${id}/promote`, {
    method: 'POST', body: JSON.stringify({ target }),
  }),
  rollbackPolicy:  (id, version) => request(`/audit/shadow/policies/${id}/rollback`, {
    method: 'POST', body: JSON.stringify({ target_version: version }),
  }),
  listVersions:    (id) => request(`/audit/shadow/policies/${id}/versions`),
  wouldHaveDenied: (id, windowHours = 24, sampleLimit = 50) => request(
    `/audit/shadow/policies/${id}/would-have-denied?window_hours=${windowHours}&sample_limit=${sampleLimit}`,
  ),
  listDecisions:   (id, params = {}) => {
    const q = new URLSearchParams()
    if (params.drift_only) q.set('drift_only', 'true')
    if (params.limit)      q.set('limit', String(params.limit))
    return request(`/audit/shadow/policies/${id}/decisions${q.toString() ? `?${q.toString()}` : ''}`)
  },
  getOnlineEval:   () => request('/audit/shadow/online-eval'),
  putOnlineEval:   (body) => request('/audit/shadow/online-eval', {
    method: 'PUT', body: JSON.stringify(body),
  }),
}

// Sprint 5 — Attack Evaluation Suite.
//   /audit/evaluation/datasets       — labelled corpora (attack + benign)
//   /audit/evaluation/evaluators     — named scorer configs
//   /audit/evaluation/jobs           — replay runs against /execute
//   /audit/evaluation/efficacy/*     — dashboard overview + per-rule trend
//
// Per-tenant scoped at the backend via the JWT claim.
export const evaluationService = {
  listDatasets:    () => request('/audit/evaluation/datasets'),
  createDataset:   (body) => request('/audit/evaluation/datasets', {
    method: 'POST', body: JSON.stringify(body),
  }),
  getDataset:      (id) => request(`/audit/evaluation/datasets/${id}`),
  listCases:       (id, params = {}) => {
    const q = new URLSearchParams()
    if (params.case_kind)      q.set('case_kind', params.case_kind)
    if (params.owasp_category) q.set('owasp_category', params.owasp_category)
    if (params.limit)          q.set('limit', String(params.limit))
    if (params.offset)         q.set('offset', String(params.offset))
    return request(`/audit/evaluation/datasets/${id}/cases${q.toString() ? `?${q.toString()}` : ''}`)
  },
  listEvaluators:  () => request('/audit/evaluation/evaluators'),
  createEvaluator: (body) => request('/audit/evaluation/evaluators', {
    method: 'POST', body: JSON.stringify(body),
  }),
  enqueueJob:      (body) => request('/audit/evaluation/jobs', {
    method: 'POST', body: JSON.stringify(body),
  }),
  listJobs:        (params = {}) => {
    const q = new URLSearchParams()
    if (params.status) q.set('status', params.status)
    if (params.limit)  q.set('limit', String(params.limit))
    return request(`/audit/evaluation/jobs${q.toString() ? `?${q.toString()}` : ''}`)
  },
  getJob:          (id) => request(`/audit/evaluation/jobs/${id}`),
  listResults:     (id, params = {}) => {
    const q = new URLSearchParams()
    if (params.only_failed)    q.set('only_failed', 'true')
    if (params.owasp_category) q.set('owasp_category', params.owasp_category)
    if (params.limit)          q.set('limit', String(params.limit))
    if (params.offset)         q.set('offset', String(params.offset))
    return request(`/audit/evaluation/jobs/${id}/results${q.toString() ? `?${q.toString()}` : ''}`)
  },
  overview:        () => request('/audit/evaluation/efficacy/overview'),
  trend:           (params = {}) => {
    const q = new URLSearchParams()
    if (params.rule_id) q.set('rule_id', params.rule_id)
    if (params.days)    q.set('days', String(params.days))
    return request(`/audit/evaluation/efficacy/trend${q.toString() ? `?${q.toString()}` : ''}`)
  },
}

export const fleetService = {
  kpis: (windowMinutes = 60) =>
    request(`/audit/fleet/kpis?window_minutes=${encodeURIComponent(windowMinutes)}`),
  timeseries: ({ metric = 'decisions', windowMinutes = 180, bucketMinutes = 5, agentId } = {}) => {
    const q = new URLSearchParams({
      metric,
      window_minutes: String(windowMinutes),
      bucket_minutes: String(bucketMinutes),
    })
    if (agentId) q.set('agent_id', agentId)
    return request(`/audit/fleet/timeseries?${q.toString()}`)
  },
  agentHealth: ({ rankBy = 'deny_rate', windowMinutes = 60, limit = 25 } = {}) =>
    request(`/audit/fleet/agent-health?rank_by=${encodeURIComponent(rankBy)}&window_minutes=${windowMinutes}&limit=${limit}`),
  recentEvents: ({ kind = 'denied', limit = 25 } = {}) =>
    request(`/audit/fleet/recent-events?kind=${encodeURIComponent(kind)}&limit=${limit}`),
  burnDown: (agentId) =>
    request(`/usage/fleet/burn-down${agentId ? `?agent_id=${encodeURIComponent(agentId)}` : ''}`),
}

export const flightService = {
  listTimelines:        (params = {}) => {
    const q = new URLSearchParams();
    if (params.minutes)  q.set('minutes',  params.minutes);
    if (params.agent_id) q.set('agent_id', params.agent_id);
    if (params.tool)     q.set('tool',     params.tool);
    if (params.status)   q.set('status',   params.status);
    if (params.limit)    q.set('limit',    params.limit);
    const qs = q.toString();
    return request(`/flight/timelines${qs ? `?${qs}` : ''}`);
  },
  getReplay:            (id)         => request(`/flight/timeline/${id}`),
  getReplayByRequest:   (rid)        => request(`/flight/timeline/by-request/${rid}`),
  getSteps:             (id)         => request(`/flight/timeline/${id}/steps`),
  // Sprint 3.3 — Decision Explorer
  getDecisionGraph:     (rid)        => request(`/flight/decision/${rid}/graph`),
  // Sprint 3.5 — Session Explorer
  listSessions:         (params = {}) => {
    const q = new URLSearchParams();
    if (params.minutes) q.set('minutes', params.minutes);
    if (params.limit)   q.set('limit',   params.limit);
    const qs = q.toString();
    return request(`/flight/sessions${qs ? `?${qs}` : ''}`);
  },
  getSession:           (sid)        => request(`/flight/sessions/${encodeURIComponent(sid)}`),
};

// 2026-05-14: Cryptographic execution receipts (ed25519). Offline-verifiable.
// 2026-05-15 (Sprint 1.3): added `verify` — server-side verify with
// historical-key fallback. UI calls this from the Flight Recorder
// receipt panel so an auditor sees a yes/no badge inline.
export const receiptService = {
  getReceipt:   (executionId) => request(`/receipts/${encodeURIComponent(executionId)}`),
  getPublicKey: ()            => request('/receipts/key'),
  verify:       (signedPayload) =>
    request('/receipts/verify', { method: 'POST', body: JSON.stringify(signedPayload) }),
};

// 2026-05-14: Daily Merkle transparency log — root commitment over receipts.
// 2026-05-15 (Sprint 1.3): added verifyRoot / listKeys / consistency so
// the Transparency page can render the full audit story (active key,
// historical keys post-rotation, chain consistency between two dates).
export const transparencyService = {
  listRoots:     (params = {}) => {
    const q = new URLSearchParams();
    if (params.since) q.set('since', params.since);
    if (params.until) q.set('until', params.until);
    if (params.limit) q.set('limit', params.limit);
    const qs = q.toString();
    return request(`/transparency/roots${qs ? `?${qs}` : ''}`);
  },
  getRoot:       (date)        => request(`/transparency/roots/${encodeURIComponent(date)}`),
  getInclusion:  (executionId) => request(`/transparency/inclusion/${encodeURIComponent(executionId)}`),
  verifyRoot:    (signedPayload) =>
    request('/transparency/verify-root', { method: 'POST', body: JSON.stringify(signedPayload) }),
  listKeys:      ()            => request('/transparency/keys'),
  consistency:   (fromDate, toDate) =>
    request(`/transparency/consistency?from_date=${encodeURIComponent(fromDate)}&to_date=${encodeURIComponent(toDate)}`),
};

// 2026-05-15 (Sprint 3.2): per-tenant quota — used by the Settings /
// Billing pages to render the rps/burst/daily/monthly limits + the
// inference cost cap with live usage counters.
export const tenantService = {
  getQuota: () => request('/tenant/quota'),
};

export const autonomyService = {
  listContracts:        (agent_id)   => request(`/autonomy/contracts${agent_id ? `?agent_id=${agent_id}` : ''}`),
  getContract:          (id)         => request(`/autonomy/contracts/${id}`),
  createContract:       (data)       => request('/autonomy/contracts', { method: 'POST', body: JSON.stringify(data) }),
  updateContract:       (id, data)   => request(`/autonomy/contracts/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  disableContract:      (id)         => request(`/autonomy/contracts/${id}`, { method: 'DELETE' }),
  listViolations:       (minutes = 1440) => request(`/autonomy/violations?minutes=${minutes}`),
  listOverrides:        (params = {}) => {
    const q = new URLSearchParams();
    if (params.minutes)     q.set('minutes',     params.minutes);
    if (params.target_kind) q.set('target_kind', params.target_kind);
    if (params.target_id)   q.set('target_id',   params.target_id);
    if (params.limit)       q.set('limit',       params.limit);
    const qs = q.toString();
    return request(`/autonomy/overrides${qs ? `?${qs}` : ''}`);
  },
  addOverride:          (body)       => request('/autonomy/overrides', {
    method: 'POST', body: JSON.stringify(body),
  }),
};

export const complianceService = {
  getEuAiAct:  (params = {}) => request(`/compliance/eu-ai-act?${new URLSearchParams(params)}`),
  getNist:     (params = {}) => request(`/compliance/nist-ai-rmf?${new URLSearchParams(params)}`),
  getSoc2:     (params = {}) => request(`/compliance/soc2?${new URLSearchParams(params)}`),
  exportPdf: (framework, startDate, endDate) => {
    const q = new URLSearchParams({ framework, start_date: startDate, end_date: endDate, format: 'pdf' })
    return blobRequest(`/compliance/export?${q}`, { method: 'POST' })
  },
  exportJsonBundle: (bundleType, periodStart, periodEnd) => {
    const q = new URLSearchParams({ period_start: periodStart, period_end: periodEnd })
    return blobRequest(`/compliance/export/${bundleType}?${q}`)
  },
  boardReport: (data) => blobRequest('/compliance/board-report', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  }),
};

export const playbookService = {
  list:          ()         => request('/playbooks'),
  getTemplates:  ()         => request('/playbooks/templates'),
  getStats:      ()         => request('/playbooks/stats'),
  create:        (data)     => request('/playbooks', { method: 'POST', body: JSON.stringify(data) }),
  get:           (id)       => request(`/playbooks/${id}`),
  update:        (id, data) => request(`/playbooks/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  remove:        (id)       => request(`/playbooks/${id}`, { method: 'DELETE' }),
  trigger:       (id, ctx)  => request(`/playbooks/${id}/trigger`, { method: 'POST', body: JSON.stringify({ context: ctx || {} }) }),
  getRuns:           (id) => request(`/playbooks/${id}/runs`),
  getAutotriggerStats: () => request('/playbooks/autotrigger-stats'),
};

export const webhookService = {
  getConfig:     ()     => request('/webhooks/config'),
  saveConfig:    (data) => request('/webhooks/config', { method: 'POST', body: JSON.stringify(data) }),
  testSlack:     (data) => request('/webhooks/test/slack', { method: 'POST', body: JSON.stringify(data || {}) }),
  testPagerduty: (data) => request('/webhooks/test/pagerduty', { method: 'POST', body: JSON.stringify(data || {}) }),
  testWebhook:   (data) => request('/webhooks/test/webhook', { method: 'POST', body: JSON.stringify(data || {}) }),
};

export const siemService = {
  getConfig:    ()     => request('/siem/config'),
  saveConfig:   (data) => request('/siem/config', { method: 'POST', body: JSON.stringify(data) }),
  testSplunk:   (data) => request('/siem/test/splunk', { method: 'POST', body: JSON.stringify(data || {}) }),
  testDatadog:  (data) => request('/siem/test/datadog', { method: 'POST', body: JSON.stringify(data || {}) }),
  push:         (data) => request('/siem/push', { method: 'POST', body: JSON.stringify(data || { limit: 100 }) }),
};

export const scheduledReportsService = {
  list:    ()           => request('/reports/scheduled'),
  create:  (data)       => request('/reports/scheduled', { method: 'POST', body: JSON.stringify(data) }),
  get:     (id)         => request(`/reports/scheduled/${id}`),
  update:  (id, data)   => request(`/reports/scheduled/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  remove:  (id)         => request(`/reports/scheduled/${id}`, { method: 'DELETE' }),
  runNow:      (id)         => request(`/reports/scheduled/${id}/run`, { method: 'POST' }),
  getHistory:  (id, limit = 20) => request(`/reports/scheduled/${id}/history?limit=${limit}`),
};

export const threatIntelService = {
  enrichIp:     (ip)     => request('/threat-intel/ip', { method: 'POST', body: JSON.stringify({ ip }) }),
  enrichDomain: (domain) => request('/threat-intel/domain', { method: 'POST', body: JSON.stringify({ domain }) }),
  getSummary:   ()       => request('/threat-intel/summary'),
};

export const notificationService = {
  list:        (params = {}) => request(`/notifications?${new URLSearchParams(params)}`),
  create:      (data)        => request('/notifications', { method: 'POST', body: JSON.stringify(data) }),
  markRead:    (id)          => request(`/notifications/${id}/read`, { method: 'POST' }),
  markAllRead: ()            => request('/notifications/read-all', { method: 'POST' }),
  getCount:    ()            => request('/notifications/count'),
};

export const ssoService = {
  getConfig:    ()     => request('/auth/sso/config'),
  saveConfig:   (data) => request('/auth/sso/config', { method: 'POST', body: JSON.stringify(data) }),
  testConfig:   (data) => request('/auth/sso/config/test', { method: 'POST', body: JSON.stringify(data || {}) }),
  getProviders: ()     => request('/auth/sso/providers'),
};

export const killSwitchService = {
  getStatus: (tenantId) => request(`/decision/kill-switch/${tenantId}`),
  triggerKill: (tenantId) =>
    request(`/decision/kill-switch/${tenantId}`, {
      method: "POST",
      body: JSON.stringify({ action: "engage" }),
    }),
  toggle: (tenantId, action) =>
    request(`/decision/kill-switch/${tenantId}`, {
      method: action === "engage" ? "POST" : "DELETE",
      body: action === "engage" ? JSON.stringify({ action: "engage" }) : undefined,
    }),
  resetKill: (tenantId) => request(`/decision/kill-switch/${tenantId}`, { method: "DELETE" }),
};

export const auditExportService = {
  export: (params) => request('/audit/export', { method: 'POST', body: JSON.stringify(params) }),
}

export const userService = {
  list: (params) => request('/users?' + new URLSearchParams(params || {})),
  invite: (data) => request('/users/invite', { method: 'POST', body: JSON.stringify(data) }),
  update: (id, data) => request(`/users/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deactivate: (id) => request(`/users/${id}`, { method: 'DELETE' }),
}

export const securityService = {
  getPosture: () => request('/security/posture'),
}

export const adminService = {
  listTenants: () => request('/admin/tenants'),
  getTenant: (id) => request(`/admin/tenants/${id}`),
}

export const demoService = {
  // Run one end-to-end Groq-agent demo. Returns the full trace.
  runGroqAgent: (payload) => request('/demo/groq-agent', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
}

// Sprint 17 — Aegis for Teams. The /team page lists each employee's
// virtual key plus today + this-month USD spend; the OWNER/ADMIN mints
// new keys here too. The /v1/messages Anthropic-proxy endpoint that
// uses these keys is server-only — the browser never calls it
// directly.
export const teamService = {
  // List every employee virtual key for the current tenant joined with
  // their Redis-tracked spend.
  listEmployees: () => request('/team/employees'),

  // Sprint 17.5 — overview rollup. Single-fetch payload powering the
  // /team hero KPIs, the Department View tab, and the Executive tab.
  // The audit_logs DB is the source of truth for 30-day spend +
  // request counts; Redis only carries today's fast-path budget
  // counter.
  overview: () => request('/team/overview'),

  // Sprint 17.6 — per-employee drill-down. Single fetch returns the
  // employee record, both budget bars, a 30-day spend trend, and the
  // last 25 audit rows for the /team/<email> detail page.
  profile: (email) =>
    request(`/team/employees/${encodeURIComponent(email)}/profile`),

  // Mint a new acp_emp_… virtual key for one employee. Returns the raw
  // key ONCE — the caller is responsible for displaying it to the
  // admin so they can hand it to the employee. After this response
  // there is no API path to recover the raw key (only the prefix).
  mintEmployeeKey: (payload) =>
    request('/api-keys/employees', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  // Revoke an employee virtual key. Re-uses the existing /api-keys/{id}
  // DELETE so we don't fork the API service.
  revokeKey: (keyId) =>
    request(`/api-keys/${encodeURIComponent(keyId)}`, { method: 'DELETE' }),
}

