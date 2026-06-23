import React, { useEffect, useRef } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { useUser } from '@clerk/react';
import MainLayout from './MainLayout';
import { clearSessionMetadata, authService } from '../../services/api';
import { getSessionItem } from '../../lib/sessionStore';

const ProtectedRoute = ({ children }) => {
  // Two truth sources during the Clerk migration:
  //   (1) Clerk session — authoritative for users that signed up via /signup.
  //   (2) Legacy session metadata — covers the existing /auth/login flow that
  //       sets an httpOnly cookie + session metadata (admin@acp.local, etc).
  //
  // ClerkAuthBridge mirrors (1) into (2) so downstream consumers (Sidebar,
  // NotificationCenter, api.js gate) keep working unchanged. Metadata lives
  // in sessionStorage (N18) so it auto-clears on tab close.
  const { isLoaded: clerkLoaded, isSignedIn: clerkSignedIn } = useUser();
  const tenantId = getSessionItem('tenant_id');
  const expiry = parseInt(getSessionItem('acp_token_expiry') || '0', 10);
  const legacyValid = !!tenantId && expiry > Date.now();
  const navigate = useNavigate();
  const verifiedRef = useRef(false);

  // Once we have successfully rendered the protected page at least once,
  // never swap back to the "syncing" screen on subsequent state churn —
  // background polls, SSE reconnects, Settings tab-switch query-string
  // navigations all cause a re-render, and any of them could read
  // `tenant_id` from sessionStorage during the brief window where some
  // unrelated fetch's 401 handler had cleared it. The original behaviour
  // unmounted the whole page on every such re-render, which the user
  // experienced as Settings tabs "blink and after that no content".
  const hasRenderedChildrenRef = useRef(false);

  const isClerkSyncing = clerkLoaded && clerkSignedIn && !tenantId;
  const isValid = (clerkLoaded && clerkSignedIn && !!tenantId) || legacyValid;

  useEffect(() => {
    // Only re-validate via /auth/me for legacy sessions — Clerk sessions are
    // self-verifying via the SDK + gateway JWKS path.
    if (!legacyValid || clerkSignedIn || verifiedRef.current) return;
    // Demo sessions also self-verify: the demo JWT carries no `typ` claim
    // so identity-svc's /auth/me handler (which requires typ=ACP_ACCESS)
    // returns 401 even though every other endpoint accepts the token.
    // Hitting /auth/me here would trigger api.js's 401 handler →
    // clearSessionMetadata → forced redirect to /login, breaking the
    // entire anonymous-demo flow. The IIFE in main.jsx already installed
    // tenant_id + role + expiry from the signed claims, so we have what
    // a successful /auth/me would have given us anyway.
    if (getSessionItem('session_kind') === 'demo') return;
    verifiedRef.current = true;

    authService.getMe()
      .then(() => {
        // Session valid
      })
      .catch((err) => {
        if (err.message && err.message.includes('UNAUTHORIZED')) {
          // api.js handles clearSessionMetadata + navigation via authEvents
        }
        // Network error — don't log out, client-side expiry is the fallback
      });
  }, [legacyValid, clerkSignedIn, navigate]);

  // Wait for Clerk to finish booting before deciding ANYTHING. Returning
  // null while Clerk is loading is correct — Clerk's own hooks throw if
  // we call them before isLoaded.
  if (!clerkLoaded && !legacyValid) {
    return <BridgeSyncingScreen label="Loading…" />;
  }

  // Bridge race: Clerk is signed in but tenant_id is still missing from
  // sessionStorage. ONLY show the syncing screen if this is the FIRST render
  // of this protected route — once we've handed off to MainLayout, never
  // unmount it on a transient tenant_id read miss. Without the
  // hasRenderedChildren gate, every Settings tab click that triggered a
  // background re-render flashed the syncing screen, blanked the page,
  // and looked broken.
  if (isClerkSyncing && !hasRenderedChildrenRef.current) {
    return <BridgeSyncingScreen label="Setting up your workspace…" />;
  }

  if (!isValid && !hasRenderedChildrenRef.current) {
    clearSessionMetadata();
    return <Navigate to="/login" replace />;
  }

  // If we already rendered children once and isValid flipped to false
  // (e.g., real session expiry), the AuthEventHandler path is in flight —
  // it will fire IncidentOverlay and navigate to /login. In the meantime
  // we keep showing the page so the user doesn't lose state mid-flow.
  hasRenderedChildrenRef.current = true;
  return <MainLayout>{children}</MainLayout>;
};

// Centered, minimal "Loading…" surface. Black background matches /login and
// /signup so we don't cause a visual flash; a tiny spinner gives the user
// confidence that the app isn't dead.
function BridgeSyncingScreen({ label }) {
  return (
    <div
      className="min-h-screen bg-[#030303] flex items-center justify-center"
      role="status"
      aria-live="polite"
    >
      <div className="flex items-center gap-3 text-xs text-neutral-500">
        <span
          className="w-3 h-3 rounded-full border border-white/20 border-t-white/70 animate-spin"
          aria-hidden="true"
        />
        <span>{label}</span>
      </div>
    </div>
  );
}

export default ProtectedRoute;
