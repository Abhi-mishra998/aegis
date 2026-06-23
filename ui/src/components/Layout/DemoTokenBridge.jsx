import { useEffect } from 'react';
import { useAuth as useAegisAuth } from '../../hooks/useAuth';
import { setSessionMetadata } from '../../services/api';

/**
 * DemoTokenBridge — consumes ?demo_token=<JWT> appended by
 * `/demo/spawn-workspace`'s redirect_url.
 *
 * Without this component, the SPA dropped the token on the floor: the
 * spawn endpoint returns `redirect_url: /dashboard?demo_token=…` but
 * nothing on the client side ever read the query param, so every
 * authenticated route bounced visitors back to /login and the demo flow
 * was silently broken end-to-end.
 *
 * What it does once on mount, before ProtectedRoute decides:
 *   1. Read ?demo_token from window.location.search.
 *   2. Decode the JWT (HS256) locally to extract tenant_id + email +
 *      role + exp without trusting the server (defense-in-depth — if
 *      the token is malformed we just abort).
 *   3. Set acp_token=<jwt> as a same-origin cookie so:
 *        - the gateway's cookie-to-Authorization bridge
 *          (services/gateway/main.py:205) attaches it on every REST call
 *        - SSE EventSource (which cannot set custom headers) authenticates
 *          via the same cookie.
 *      The cookie is intentionally NOT httpOnly because document.cookie
 *      cannot create httpOnly cookies. For the 30-minute demo token that
 *      is acceptable; full-tenant Clerk sessions still use the httpOnly
 *      acp_token issued by /auth/clerk/provision.
 *   4. Mirror tenant_id + role + email into sessionStorage via
 *      setSessionMetadata so ProtectedRoute, api.js, and the sidebar
 *      see an authenticated session.
 *   5. Strip ?demo_token= from the URL bar via history.replaceState so
 *      a copy-paste of the URL doesn't leak the token.
 */
export default function DemoTokenBridge() {
  const { updateAuth } = useAegisAuth();

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const demoToken = params.get('demo_token');
    if (!demoToken) return;

    let claims = {};
    try {
      const parts = demoToken.split('.');
      if (parts.length !== 3) throw new Error('not a JWT');
      const b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
      claims = JSON.parse(atob(b64));
    } catch (err) {
      console.warn('DemoTokenBridge: malformed demo_token, ignoring', err);
      return;
    }

    const nowSec = Math.floor(Date.now() / 1000);
    const expSec = Number(claims.exp || 0);
    if (!expSec || expSec <= nowSec) {
      console.warn('DemoTokenBridge: demo_token already expired');
      return;
    }
    const ttlSeconds = expSec - nowSec;

    const tenantId = claims.tenant_id || claims.aegis_tenant_id;
    const email = claims.sub || claims.email || claims.user_email || 'demo';
    const role = String(claims.role || claims.aegis_role || 'OWNER').toUpperCase();

    if (!tenantId) {
      console.warn('DemoTokenBridge: demo_token has no tenant_id claim');
      return;
    }

    const isSecure = window.location.protocol === 'https:';
    const cookieAttrs = [
      `acp_token=${demoToken}`,
      'path=/',
      `max-age=${ttlSeconds}`,
      'samesite=Strict',
    ];
    if (isSecure) cookieAttrs.push('Secure');
    document.cookie = cookieAttrs.join('; ');

    setSessionMetadata({
      tenant_id: tenantId,
      user_email: email,
      role,
      expires_in: ttlSeconds,
    });

    updateAuth({
      isAuthenticated: true,
      user: email,
      tenant_id: tenantId,
      role,
      token: null,
    });

    params.delete('demo_token');
    const cleanUrl =
      window.location.pathname +
      (params.toString() ? `?${params.toString()}` : '') +
      window.location.hash;
    window.history.replaceState({}, '', cleanUrl);
  }, [updateAuth]);

  return null;
}
