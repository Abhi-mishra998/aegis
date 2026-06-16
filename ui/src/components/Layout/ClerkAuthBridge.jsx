import { useEffect } from 'react';
import { useUser, useAuth as useClerkAuth, useOrganization } from '@clerk/react';
import { useAuth as useAegisAuth } from '../../hooks/useAuth';
import { setSessionMetadata, clearSessionMetadata } from '../../services/api';
import { setClerkTokenGetter } from '../../services/clerkAuth';

const API_BASE = import.meta.env.VITE_GATEWAY_URL || '';

async function clerkProvision(token) {
  if (!token) return null;
  try {
    const resp = await fetch(`${API_BASE}/auth/clerk/provision`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      credentials: 'include',
    });
    if (!resp.ok) {
      console.warn('clerkProvision: non-2xx response', resp.status);
      return null;
    }
    const body = await resp.json().catch(() => null);
    return body?.data || null;
  } catch (err) {
    console.warn('clerkProvision: fetch failed', err);
    return null;
  }
}

const CLERK_ROLE_TO_AEGIS = {
  'org:owner': 'OWNER',
  'org:admin': 'ADMIN',
  'org:security_analyst': 'SECURITY_ANALYST',
  'org:developer': 'DEVELOPER',
  'org:read_only': 'READ_ONLY',
};

function normalizeRole(rawRole) {
  if (!rawRole) return 'OWNER';
  const mapped = CLERK_ROLE_TO_AEGIS[rawRole];
  if (mapped) return mapped;
  return String(rawRole).toUpperCase().replace(/^ORG:/, '');
}

function decodeJwtPayload(token) {
  if (!token || typeof token !== 'string') return {};
  const parts = token.split('.');
  if (parts.length !== 3) return {};
  try {
    const base64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    return JSON.parse(atob(base64));
  } catch (err) {
    return {};
  }
}

/**
 * ClerkAuthBridge — mirrors Clerk's session state into the legacy
 * AuthContext + localStorage so the rest of the app (ProtectedRoute,
 * Sidebar, NotificationCenter, all 30+ pages) keeps working with no
 * Clerk awareness of its own.
 *
 * On Clerk sign-in:
 *   - Calls getToken({template: 'aegis'}) to receive a JWT carrying
 *     aegis_tenant_id + aegis_role + email claims.
 *   - Decodes the payload and writes session metadata to localStorage.
 *   - Registers the token-getter so api.js can attach Authorization
 *     on every backend call.
 *
 * On Clerk sign-out:
 *   - Clears session metadata + AuthContext, unregisters the getter.
 *
 * Lives inside ClerkProvider AND AuthContext.Provider in App.jsx.
 */
export default function ClerkAuthBridge() {
  const { isLoaded: userLoaded, isSignedIn, user } = useUser();
  const { getToken } = useClerkAuth();
  const { organization, membership, isLoaded: orgLoaded } = useOrganization();
  const { updateAuth } = useAegisAuth();

  useEffect(() => {
    if (!userLoaded || !orgLoaded) return;

    if (!isSignedIn) {
      setClerkTokenGetter(null);
      clearSessionMetadata();
      updateAuth({
        isAuthenticated: false,
        user: null,
        tenant_id: null,
        role: null,
        token: null,
      });
      return;
    }

    // Prefer the `aegis` JWT template (carries aegis_tenant_id +
    // aegis_role + email claims). If the operator hasn't created the
    // template in the Clerk dashboard yet, fall through to the default
    // session token — the gateway resolves the rest via the org→tenant
    // mapping the webhook receiver caches in Redis.
    const fetchAegisToken = async () => {
      try {
        const token = await getToken({ template: 'aegis' });
        if (token) return token;
      } catch (err) {
        // Template missing — fall through to default token.
      }
      return getToken();
    };
    setClerkTokenGetter(fetchAegisToken);

    let cancelled = false;

    (async () => {
      let payload = {};
      let token = null;
      try {
        token = await fetchAegisToken();
        payload = decodeJwtPayload(token);
      } catch (err) {
        console.warn(
          'ClerkAuthBridge: token fetch failed; falling back to user metadata',
          err,
        );
      }
      if (cancelled) return;

      let tenantId =
        payload.aegis_tenant_id ||
        organization?.publicMetadata?.aegis_tenant_id ||
        '';
      let role = normalizeRole(
        payload.aegis_role || membership?.role || 'org:owner',
      );
      const email = user?.primaryEmailAddress?.emailAddress || '';

      // Signup → first-request race: the webhook may not have landed yet,
      // so the JWT carries no aegis_tenant_id and org.publicMetadata is
      // empty. Synchronously provision via /auth/clerk/provision before
      // we mark the session live; the endpoint is idempotent so a
      // late-arriving webhook is harmless.
      //
      // Called on EVERY sign-in (not just when tenantId is missing) — the
      // gateway side of this endpoint also sets the acp_token httpOnly
      // cookie that the SSE EventSource needs. Without that cookie the
      // browser shows "Syncing" forever because EventSource can't attach
      // the Authorization header.
      if (token) {
        const provisioned = await clerkProvision(token);
        if (cancelled) return;
        if (!tenantId && provisioned?.tenant_id) {
          tenantId = provisioned.tenant_id;
          if (provisioned.role) {
            role = normalizeRole(provisioned.role);
          }
        }
      }

      const expiresIn = payload.exp
        ? Math.max(60, payload.exp - Math.floor(Date.now() / 1000))
        : 3600;

      setSessionMetadata({
        tenant_id: tenantId,
        expires_in: expiresIn,
        user_email: email,
        role,
      });
      updateAuth({
        isAuthenticated: true,
        user: email,
        tenant_id: tenantId || null,
        role,
        token: null,
      });
    })();

    return () => {
      cancelled = true;
    };
  }, [
    userLoaded,
    orgLoaded,
    isSignedIn,
    user,
    organization,
    membership,
    getToken,
    updateAuth,
  ]);

  // Periodic refresh — Clerk's default JWT lifetime is 60 seconds. Without
  // this loop the localStorage acp_token_expiry counts down to zero,
  // App.jsx fires session_expired, IncidentOverlay pops up, and the
  // acp_token cookie that backs the SSE EventSource goes stale (SSE drops
  // to "Disconnected — network error"). Refresh every 45s so we replace
  // both the cookie and the expiry timestamp BEFORE the 60s window
  // closes.
  useEffect(() => {
    if (!userLoaded || !orgLoaded || !isSignedIn) return;

    let cancelled = false;
    const REFRESH_INTERVAL_MS = 45_000;

    const refresh = async () => {
      try {
        const token = await getToken({ template: 'aegis' }).catch(() => getToken());
        if (cancelled || !token) return;
        // Re-call /auth/clerk/provision so the gateway re-issues the
        // acp_token cookie with the freshly-refreshed Clerk JWT.
        // Idempotent — late-arriving Clerk webhook can't corrupt anything.
        await clerkProvision(token);
        if (cancelled) return;
        // Push the new expiry into localStorage so App.jsx's session
        // timer resets.
        const payload = decodeJwtPayload(token);
        const expiresIn = payload.exp
          ? Math.max(60, payload.exp - Math.floor(Date.now() / 1000))
          : 3600;
        // setSessionMetadata writes acp_token_expiry = Date.now() + expires_in*1000.
        // Only re-write the timestamp; preserve everything else that ClerkAuthBridge's
        // primary effect already populated.
        const tenantId   = localStorage.getItem('tenant_id') || '';
        const userEmail  = localStorage.getItem('user_email') || '';
        const role       = localStorage.getItem('user_role') || '';
        setSessionMetadata({
          tenant_id:   tenantId,
          expires_in:  expiresIn,
          user_email:  userEmail,
          role,
        });
      } catch (err) {
        // Silent — Clerk's own session manager retries; we just don't want to
        // crash the loop on a transient network blip.
      }
    };

    // Kick once after a short delay so the primary sign-in effect has
    // already populated localStorage. Then run on a fixed interval.
    const kick = setTimeout(refresh, 5_000);
    const interval = setInterval(refresh, REFRESH_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearTimeout(kick);
      clearInterval(interval);
    };
  }, [userLoaded, orgLoaded, isSignedIn, getToken]);

  return null;
}
