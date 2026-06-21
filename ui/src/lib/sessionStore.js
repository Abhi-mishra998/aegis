/**
 * sessionStore — typed wrapper over `sessionStorage` for auth-related
 * session metadata (tenant_id, expiry, role, etc.).
 *
 * Why sessionStorage instead of localStorage:
 *   - sessionStorage is bound to the tab and is wiped on tab close, which
 *     gives auth metadata an upper-bound lifetime that matches the user's
 *     active session — not "until the browser is uninstalled".
 *   - More importantly, an XSS sink anywhere in the app could read
 *     localStorage at will (it has the same readability semantics as any
 *     other client-side variable, in contrast to HttpOnly cookies).
 *     sessionStorage shares the XSS-readable property, but the smaller
 *     time window + per-tab scoping limits the blast radius if a future
 *     XSS lands in production.
 *
 * The real Clerk JWT is NEVER stored anywhere — it lives in Clerk's SDK
 * memory and the httpOnly `acp_token` cookie that the gateway sets. The
 * keys here are non-secret session metadata only.
 *
 * Guarded with try/catch so the app does not blow up in browsers /
 * private-mode contexts where sessionStorage throws (Safari ITP, etc.).
 *
 * Cross-tab sync note: sessionStorage is per-tab, so the `storage` event
 * does NOT fire across tabs the way it did for localStorage. That is the
 * intended security boundary — each tab carries its own Clerk session
 * and ClerkAuthBridge will mirror metadata into the new tab on mount.
 */

const _storage = (() => {
  try {
    if (typeof window === 'undefined') return null;
    return window.sessionStorage;
  } catch {
    return null;
  }
})();

export function getSessionItem(key) {
  if (!_storage) return null;
  try {
    return _storage.getItem(key);
  } catch {
    return null;
  }
}

export function setSessionItem(key, value) {
  if (!_storage) return;
  try {
    _storage.setItem(key, value);
  } catch {
    /* quota / disabled — best-effort */
  }
}

export function removeSessionItem(key) {
  if (!_storage) return;
  try {
    _storage.removeItem(key);
  } catch {
    /* ignore */
  }
}
