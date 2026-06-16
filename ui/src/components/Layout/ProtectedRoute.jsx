import React, { useEffect, useRef } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { useUser } from '@clerk/react';
import MainLayout from './MainLayout';
import { clearSessionMetadata, authService } from '../../services/api';

const ProtectedRoute = ({ children }) => {
  // Two truth sources during the Clerk migration:
  //   (1) Clerk session — authoritative for users that signed up via /signup.
  //   (2) Legacy localStorage — covers the existing /auth/login flow that
  //       sets an httpOnly cookie + session metadata (admin@acp.local, etc).
  //
  // ClerkAuthBridge mirrors (1) into (2) so downstream consumers (Sidebar,
  // NotificationCenter, api.js gate) keep working unchanged.
  const { isLoaded: clerkLoaded, isSignedIn: clerkSignedIn } = useUser();
  const tenantId = localStorage.getItem('tenant_id');
  const expiry = parseInt(localStorage.getItem('acp_token_expiry') || '0', 10);
  const legacyValid = !!tenantId && expiry > Date.now();
  const navigate = useNavigate();
  const verifiedRef = useRef(false);

  // Bridge race: Clerk is signed in but ClerkAuthBridge has not yet written
  // tenant_id to localStorage (the post-signin IIFE is mid-flight or the
  // /auth/clerk/provision response is still in-flight). If we render the
  // child page now, its first API call ships with no X-Tenant-ID and the
  // gateway 401s — the user lands on an error-banner Dashboard. We hold
  // the route in a "syncing" state until tenant_id arrives or Clerk is
  // confirmed signed-out.
  const isClerkSyncing = clerkLoaded && clerkSignedIn && !tenantId;
  const isValid = (clerkLoaded && clerkSignedIn && !!tenantId) || legacyValid;

  useEffect(() => {
    // Only re-validate via /auth/me for legacy sessions — Clerk sessions are
    // self-verifying via the SDK + gateway JWKS path.
    if (!legacyValid || clerkSignedIn || verifiedRef.current) return;
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

  // Wait for Clerk to load before deciding — prevents a flash redirect to
  // /login during the first render of a signed-in Clerk session.
  if (!clerkLoaded && !legacyValid) {
    return <BridgeSyncingScreen label="Loading…" />;
  }

  // Clerk says signed-in but the bridge has not finished syncing. Show a
  // minimal loading screen instead of letting the protected page mount
  // with an empty tenant_id (which would 401 the page's first fetch).
  if (isClerkSyncing) {
    return <BridgeSyncingScreen label="Setting up your workspace…" />;
  }

  if (!isValid) {
    clearSessionMetadata();
    return <Navigate to="/login" replace />;
  }

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
