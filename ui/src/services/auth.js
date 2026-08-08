/**
 * First-party auth service — email + password against the identity
 * service's /auth/register, /auth/token, and /auth/password/* endpoints.
 *
 * M12 closure 2026-07-31: the JWT is NO LONGER stored client-side. The
 * gateway sets `acp_token` as an httpOnly Secure SameSite=Strict cookie
 * on /auth/token + /auth/register responses; the browser auto-sends it
 * on every fetch via `credentials: 'include'`. sessionStorage now holds
 * only non-sensitive metadata (tenant_id, user_email, user_role, expiry)
 * so ProtectedRoute + the sidebar can render without a server round-trip.
 *
 * // TODO(server): set httpOnly cookie on /auth/token + /auth/register —
 * depends on identity server change (owned by auth-batch agent). Until
 * that ships, requests will 401 because there is no client-side token to
 * fall back on. Do NOT re-introduce client-side storage as a bridge —
 * that just puts the XSS-exfil surface back.
 */

import { setSessionMetadata, clearSessionMetadata } from "./api";
import { setSessionItem, removeSessionItem, getSessionItem } from "../lib/sessionStore";
import { logger } from "../lib/logger";

const API_BASE = import.meta.env.VITE_GATEWAY_URL || "";

function persistSession({ expires_in, tenant_id, user_id, role }, email) {
  // ponytail: JWT lives in httpOnly cookie now, browser auto-sends via credentials:'include'
  setSessionMetadata({
    tenant_id,
    user_email: email,
    role,
    agent_id: null,
    expires_in,
  });
  if (user_id) setSessionItem("user_id", String(user_id));
}

export function getAccessToken() {
  // ponytail: no client-side token anymore — cookie is httpOnly, invisible to JS.
  // Retained as a stub so legacy callers (see api.js parseApiError, changePassword)
  // don't have to change shape. Returning null means "cannot attach a Bearer here."
  return null;
}

export function hasSession() {
  // Session presence inferred from metadata + not-yet-expired timestamp.
  // Server is the authoritative validator; any 401 clears the session.
  const tenantId = getSessionItem("tenant_id");
  if (!tenantId) return false;
  const expiry = parseInt(getSessionItem("acp_token_expiry") || "0", 10);
  return expiry > Date.now();
}

/** No-op — browser auto-sends the httpOnly cookie via credentials:'include'. */
export async function attachAuth(headers) {
  // ponytail: JWT lives in httpOnly cookie now, browser auto-sends via credentials:'include'
  return headers;
}

export function clearSession() {
  removeSessionItem("user_id");
  clearSessionMetadata();
  // Cookie is httpOnly — cannot be cleared from JS. The gateway's
  // /auth/logout response must include `Set-Cookie: acp_token=; Max-Age=0`.
  // See logout() below.
}

async function post(path, body, extraHeaders = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...extraHeaders },
    body: JSON.stringify(body),
    credentials: "include",
  });
  const text = await res.text();
  let json = {};
  try { json = text ? JSON.parse(text) : {}; } catch {}
  if (!res.ok) {
    const detail = json?.detail || json?.error || `Request failed (${res.status})`;
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    err._status = res.status;
    err._body = json;
    throw err;
  }
  return json;
}

export async function login({ email, password }) {
  const body = { email: String(email).trim().toLowerCase(), password };
  // TODO(server): set httpOnly cookie on /auth/token — depends on identity server change.
  // Server should keep returning {tenant_id, user_id, role, expires_in} in the body
  // (used to seed sessionStorage metadata) but the JWT itself moves into the cookie.
  const resp = await post("/auth/token", body);
  const data = resp?.data || {};
  if (!data.tenant_id) {
    throw new Error("Login succeeded but no session metadata returned");
  }
  persistSession(data, body.email);
  return data;
}

export async function register({ email, password, workspace_name, full_name }) {
  // TODO(server): set httpOnly cookie on /auth/register — depends on identity server change.
  const resp = await post("/auth/register", {
    email: String(email).trim().toLowerCase(),
    password,
    workspace_name: workspace_name || undefined,
    full_name: full_name || undefined,
  });
  const data = resp?.data || {};
  // SEC-2026-07-31 (L1): duplicate-email registrations now return
  // 202 with `{status: "accepted", check_email: true}` instead of
  // 409 (which was a user-enumeration oracle). Surface that shape
  // to the caller so the sign-up UI can render a neutral
  // "check your inbox" message without leaking existence.
  if (data && data.check_email) {
    return { status: "accepted", check_email: true };
  }
  if (!data.tenant_id) {
    throw new Error("Registration succeeded but no session metadata returned");
  }
  persistSession(data, String(email).trim().toLowerCase());
  return data;
}

export async function requestPasswordReset(email) {
  // Always resolves — the endpoint is 202 regardless of whether the email
  // is registered, to prevent enumeration.
  try {
    await post("/auth/password/reset-request", {
      email: String(email).trim().toLowerCase(),
    });
  } catch (err) {
    // 429 is the only real error the client should surface.
    if (err._status === 429) throw err;
    logger.warn("password_reset_request soft-fail", err);
  }
  return { status: "accepted" };
}

export async function confirmPasswordReset({ token, new_password }) {
  const resp = await post("/auth/password/reset-confirm", { token, new_password });
  const data = resp?.data || {};
  if (data.tenant_id) {
    // Reset-confirm returns a fresh session — server issues the cookie.
    persistSession(data, "");
  }
  return data;
}

export async function changePassword({ current_password, new_password }) {
  if (!hasSession()) throw new Error("Not signed in");
  // ponytail: cookie auth — no Authorization header needed
  return post("/auth/password/change", { current_password, new_password });
}

export async function logout() {
  // Best-effort server-side revoke; local metadata clear happens regardless.
  // The server's /auth/logout response must include `Set-Cookie: acp_token=; Max-Age=0`
  // to actually clear the httpOnly cookie — JS cannot do it.
  try {
    await fetch(`${API_BASE}/auth/logout`, {
      method: "POST",
      credentials: "include",
    });
  } catch {
    // Network failure on logout still clears local state.
  }
  clearSession();
}
