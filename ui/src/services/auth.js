/**
 * First-party auth service — email + password against the identity
 * service's /auth/register, /auth/token, and /auth/password/* endpoints.
 *
 * The access token is stored in sessionStorage (per-tab, auto-clears on
 * tab close so a future XSS sink can't exfiltrate past that tab). It's
 * ALSO mirrored to a SameSite=Strict cookie so SSE (EventSource, which
 * can't attach an Authorization header) has a credential to present.
 *
 * We don't have httpOnly cookies here because JS has to set them after
 * a successful login. If your deployment adds a gateway-side Set-Cookie
 * on /auth/token, this module is compatible — it just doesn't rely on it.
 */

import { setSessionMetadata, clearSessionMetadata } from "./api";
import { setSessionItem, removeSessionItem, getSessionItem } from "../lib/sessionStore";
import { logger } from "../lib/logger";

const API_BASE = import.meta.env.VITE_GATEWAY_URL || "";
const TOKEN_KEY = "acp_access_token";

function decodeJwtExpMs(token) {
  if (!token || typeof token !== "string") return 0;
  const parts = token.split(".");
  if (parts.length !== 3) return 0;
  try {
    const b64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const claims = JSON.parse(atob(b64));
    return typeof claims.exp === "number" ? claims.exp * 1000 : 0;
  } catch {
    return 0;
  }
}

function persistSession({ access_token, expires_in, tenant_id, user_id, role }, email) {
  setSessionItem(TOKEN_KEY, access_token);
  setSessionMetadata({
    tenant_id,
    user_email: email,
    role,
    agent_id: null,
    expires_in,
  });
  if (user_id) setSessionItem("user_id", String(user_id));

  const isSecure = window.location.protocol === "https:";
  const ttl = Math.max(60, Number(expires_in) || 3600);
  const attrs = [
    `acp_token=${access_token}`,
    "path=/",
    `max-age=${ttl}`,
    "samesite=Strict",
  ];
  if (isSecure) attrs.push("Secure");
  document.cookie = attrs.join("; ");
}

export function getAccessToken() {
  const t = getSessionItem(TOKEN_KEY);
  if (!t) return null;
  const exp = decodeJwtExpMs(t);
  if (exp > 0 && exp <= Date.now()) return null;
  return t;
}

export function hasSession() {
  return getAccessToken() !== null;
}

/** Attach Authorization: Bearer if a valid session token is present. */
export async function attachAuth(headers) {
  const t = getAccessToken();
  if (t) headers.Authorization = `Bearer ${t}`;
  return headers;
}

export function clearSession() {
  removeSessionItem(TOKEN_KEY);
  removeSessionItem("user_id");
  clearSessionMetadata();
  document.cookie = "acp_token=; path=/; max-age=0; samesite=Strict";
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
  const resp = await post("/auth/token", body);
  const data = resp?.data || {};
  if (!data.access_token) {
    throw new Error("Login succeeded but no access token returned");
  }
  persistSession(data, body.email);
  return data;
}

export async function register({ email, password, workspace_name, full_name }) {
  const resp = await post("/auth/register", {
    email: String(email).trim().toLowerCase(),
    password,
    workspace_name: workspace_name || undefined,
    full_name: full_name || undefined,
  });
  const data = resp?.data || {};
  if (!data.access_token) {
    throw new Error("Registration succeeded but no access token returned");
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
  if (data.access_token) {
    // Reset-confirm returns a fresh session — log the user in.
    persistSession(data, "");
  }
  return data;
}

export async function changePassword({ current_password, new_password }) {
  const t = getAccessToken();
  if (!t) throw new Error("Not signed in");
  return post(
    "/auth/password/change",
    { current_password, new_password },
    { Authorization: `Bearer ${t}` },
  );
}

export async function logout() {
  // Best-effort server-side revoke; client-side clear happens regardless.
  const t = getAccessToken();
  if (t) {
    try {
      await fetch(`${API_BASE}/auth/logout`, {
        method: "POST",
        headers: { Authorization: `Bearer ${t}` },
        credentials: "include",
      });
    } catch {
      // Network failure on logout still clears local state.
    }
  }
  clearSession();
}
