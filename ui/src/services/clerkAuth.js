/**
 * Clerk → Aegis token bridge.
 *
 * The Clerk React SDK exposes its JWT via the `useAuth().getToken` hook, which
 * is only callable from inside a React component tree. api.js is a plain
 * module — it can't call hooks. So ClerkAuthBridge registers a token-getter
 * here on mount, and api.js calls `attachClerkAuth(headers)` before each fetch.
 *
 * When the user is signed out, the getter is cleared and `attachClerkAuth`
 * becomes a no-op — the legacy cookie path is what the request falls back to.
 *
 * Clerk's default JWT TTL is 60 seconds. If we naively return whatever
 * `getToken()` hands us, the token can be 1–2 s from expiry by the time it
 * lands at the gateway, which then 401s with "Clerk token has expired".
 * `getClerkToken()` peeks at the JWT's `exp` claim and asks Clerk for a fresh
 * one when fewer than `REFRESH_WHEN_REMAINING_MS` milliseconds are left.
 *
 * Contract for `_clerkTokenGetter`: `(options?: {skipCache?: boolean}) =>
 * Promise<string|null>`. ClerkAuthBridge implements this by passing the
 * option through to Clerk's `getToken({template:'aegis', skipCache})`.
 */

let _clerkTokenGetter = null;

// Force a fresh token from Clerk when the cached one has less than 10s left.
// Picked to safely cover gateway-validation latency (≤300 ms p99) + any clock
// skew between browser and server (RFC 7519 recommends ≤2 s tolerance).
const REFRESH_WHEN_REMAINING_MS = 10_000;

export function setClerkTokenGetter(fn) {
  _clerkTokenGetter = typeof fn === 'function' ? fn : null;
}

/** True when ClerkAuthBridge has registered a getter — i.e. a Clerk session is active. */
export function hasClerkAuth() {
  return _clerkTokenGetter !== null;
}

function _decodeExpMs(token) {
  if (!token || typeof token !== 'string') return 0;
  const parts = token.split('.');
  if (parts.length !== 3) return 0;
  try {
    const json = JSON.parse(
      atob(parts[1].replace(/-/g, '+').replace(/_/g, '/'))
    );
    return typeof json.exp === 'number' ? json.exp * 1000 : 0;
  } catch {
    return 0;
  }
}

export async function getClerkToken() {
  if (!_clerkTokenGetter) return null;
  try {
    let token = await _clerkTokenGetter();
    if (!token) return null;
    const expMs = _decodeExpMs(token);
    if (expMs > 0 && expMs - Date.now() < REFRESH_WHEN_REMAINING_MS) {
      // Token is about to expire (or already has). Force a network round-
      // trip to Clerk so the gateway never sees an expired Bearer.
      try {
        const fresh = await _clerkTokenGetter({ skipCache: true });
        if (fresh) token = fresh;
      } catch {
        // Best-effort: fall through with the original token. The
        // retry-on-401 in api.js handles the network-failure case.
      }
    }
    return token;
  } catch (err) {
    console.warn('clerkAuth: getToken failed', err);
    return null;
  }
}

/**
 * Force a fresh Clerk token (bypassing the SDK's in-memory cache). Used by
 * api.js's retry-on-401 path: if the gateway just rejected the previous
 * token as expired, asking Clerk to re-fetch is the right next move.
 */
export async function getFreshClerkToken() {
  if (!_clerkTokenGetter) return null;
  try {
    return await _clerkTokenGetter({ skipCache: true });
  } catch (err) {
    console.warn('clerkAuth: getFreshClerkToken failed', err);
    return null;
  }
}

export async function attachClerkAuth(headers) {
  const token = await getClerkToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}
