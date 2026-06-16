import { useEffect, useRef } from 'react';
import { useUser, useAuth as useClerkAuth, useOrganization } from '@clerk/react';
import { useAuth as useAegisAuth } from '../../hooks/useAuth';
import { setSessionMetadata, clearSessionMetadata } from '../../services/api';
import { setClerkTokenGetter } from '../../services/clerkAuth';
import { emitAuthFailure } from '../../lib/authEvents';

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

// Consecutive refresh failures that we tolerate before raising a hard
// auth_failure event. Five * 10s poll interval = ~50s of silent retry
// before the user sees the SOC incident overlay — enough to ride out a
// brief Clerk hiccup, short enough that a permanently-broken session
// surfaces fast.
const REFRESH_FAILURE_BUDGET = 5;

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

  // Stable ref for the latest getToken. Clerk's hook returns a NEW
  // function reference on most re-renders; putting getToken directly into
  // the dependency arrays of the primary AND refresh effects below caused
  // each one to tear down + re-arm on every render. That made the
  // primary IIFE fire `/auth/clerk/provision` repeatedly and the refresh
  // interval never get to tick.
  const getTokenRef = useRef(getToken);
  useEffect(() => { getTokenRef.current = getToken; }, [getToken]);

  // tenant_id we last mirrored — used to skip provision on benign
  // re-renders where the organization/membership refs churn but nothing
  // about the signed-in identity actually changed.
  const lastMirroredRef = useRef({
    clerkUserId: null,
    clerkOrgId:  null,
  });

  useEffect(() => {
    if (!userLoaded || !orgLoaded) return;

    if (!isSignedIn) {
      setClerkTokenGetter(null);
      clearSessionMetadata();
      lastMirroredRef.current = { clerkUserId: null, clerkOrgId: null };
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
    //
    // Accepts `options.skipCache` so api.js's retry-on-401 path can ask
    // Clerk to bypass its in-memory cache and re-fetch from the network.
    const fetchAegisToken = async (options) => {
      const get = getTokenRef.current;
      const skipCache = Boolean(options && options.skipCache);
      try {
        const token = await get({ template: 'aegis', skipCache });
        if (token) return token;
      } catch (err) {
        // Template missing — fall through to default token.
      }
      return get({ skipCache });
    };
    setClerkTokenGetter(fetchAegisToken);

    // Skip the heavy provision if we have already mirrored this exact
    // (Clerk user, Clerk org) tuple in this tab. The clerk_user_id +
    // org_id from the JWT are the identity that matters; downstream
    // identity churn (membership permission shuffle, org name edit)
    // doesn't require a re-provision.
    const currentUserId = user?.id || null;
    const currentOrgId  = organization?.id || null;
    const alreadyMirrored =
      lastMirroredRef.current.clerkUserId === currentUserId &&
      lastMirroredRef.current.clerkOrgId  === currentOrgId &&
      Boolean(localStorage.getItem('tenant_id'));

    if (alreadyMirrored) {
      // The refresh effect below will handle JWT rotation; nothing to
      // do here.
      return;
    }

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
      // Also sets the acp_token httpOnly cookie that the SSE EventSource
      // needs (browser EventSource can't attach the Authorization header).
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
      lastMirroredRef.current = {
        clerkUserId: currentUserId,
        clerkOrgId:  currentOrgId,
      };
    })();

    return () => {
      cancelled = true;
    };
    // Deps: only the booleans + identifying tuples that actually change
    // what we'd write. getToken is intentionally NOT here — its ref churns
    // on every render and the latest function lives on getTokenRef.
  }, [
    userLoaded,
    orgLoaded,
    isSignedIn,
    user?.id,
    organization?.id,
    membership?.role,
    user?.primaryEmailAddress?.emailAddress,
    updateAuth,
  ]);

  // Periodic refresh — Clerk's default JWT lifetime is 60 seconds. Without
  // this loop the cookie expires, SSE handshake 401s, App.jsx fires
  // session_expired, and the IncidentOverlay pops up.
  //
  // Strategy: poll every 10 seconds. Only refresh the cookie when the
  // current JWT is < 25 seconds from expiry. That gives us:
  //   - At most one Clerk getToken() call per 30 seconds of session
  //   - Cookie always refreshed at least 25s before expiry, so the
  //     SSE never sees an expired token at handshake.
  //
  // Failure handling: count consecutive failures and after
  // REFRESH_FAILURE_BUDGET in a row, fire an auth_failure event so the
  // user sees the SOC overlay instead of an indefinitely-frozen session.
  useEffect(() => {
    if (!userLoaded || !orgLoaded || !isSignedIn) return;

    let cancelled = false;
    let inFlight = false;
    let consecutiveFailures = 0;
    // Poll every 5s and rotate the cookie whenever the current JWT has
    // less than 40s left. Clerk's default JWT lifetime is 60s, so this
    // gives us a >35s overlap between cookie rotations — the SSE
    // EventSource never sees an already-expired Bearer at handshake.
    const POLL_INTERVAL_MS    = 5_000;
    const REFRESH_AHEAD_MS    = 40_000;

    const refresh = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const fetchToken = getTokenRef.current;
        // skipCache: force Clerk to hit the network so we don't re-mint
        // the cookie with the same about-to-expire token the SDK has in
        // its in-memory cache.
        const token = await fetchToken({ template: 'aegis', skipCache: true })
          .catch(() => fetchToken({ skipCache: true }));
        if (cancelled || !token) {
          if (!cancelled && !token) {
            consecutiveFailures += 1;
          }
          return;
        }
        // Re-call /auth/clerk/provision so the gateway re-issues the
        // acp_token cookie with the freshly-refreshed Clerk JWT.
        // Idempotent.
        const provisioned = await clerkProvision(token);
        if (cancelled) return;
        if (!provisioned) {
          consecutiveFailures += 1;
        } else {
          consecutiveFailures = 0;
        }
        // Update the session-timer's expiry so App.jsx's 5s poll
        // doesn't fire session_expired.
        const payload = decodeJwtPayload(token);
        const expiresIn = payload.exp
          ? Math.max(60, payload.exp - Math.floor(Date.now() / 1000))
          : 3600;
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
        consecutiveFailures += 1;
        console.warn('ClerkAuthBridge: refresh tick failed', err);
      } finally {
        inFlight = false;
        if (consecutiveFailures >= REFRESH_FAILURE_BUDGET && !cancelled) {
          // Stop the loop and surface the failure. The incident handler
          // in App.jsx will clear localStorage + bounce to /login.
          cancelled = true;
          emitAuthFailure({
            reason: 'refresh_failed',
            url: '/auth/clerk/provision',
            statusCode: null,
          });
        }
      }
    };

    const tick = () => {
      if (cancelled) return;
      const expiryMs = parseInt(localStorage.getItem('acp_token_expiry') || '0', 10);
      const remaining = expiryMs - Date.now();
      // First load: localStorage hasn't been populated yet (the primary
      // sign-in effect hasn't completed). Refresh immediately so the
      // gateway gets the cookie before any page mounts.
      if (expiryMs === 0 || remaining < REFRESH_AHEAD_MS) {
        refresh();
      }
    };

    // Kick after 2s so the primary effect has had time to land its
    // localStorage write. Then poll every 10s.
    const kick = setTimeout(tick, 2_000);
    const interval = setInterval(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearTimeout(kick);
      clearInterval(interval);
    };
  }, [userLoaded, orgLoaded, isSignedIn]);

  return null;
}
