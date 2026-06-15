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
 */

let _clerkTokenGetter = null;

export function setClerkTokenGetter(fn) {
  _clerkTokenGetter = typeof fn === 'function' ? fn : null;
}

/** True when ClerkAuthBridge has registered a getter — i.e. a Clerk session is active. */
export function hasClerkAuth() {
  return _clerkTokenGetter !== null;
}

export async function getClerkToken() {
  if (!_clerkTokenGetter) return null;
  try {
    return await _clerkTokenGetter();
  } catch (err) {
    console.warn('clerkAuth: getToken failed', err);
    return null;
  }
}

export async function attachClerkAuth(headers) {
  const token = await getClerkToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}
