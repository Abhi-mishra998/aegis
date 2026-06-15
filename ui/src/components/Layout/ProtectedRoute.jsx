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
  // NotificationCenter, api.js gate) keep working unchanged. We accept
  // either truth source here to avoid a redirect during the mirror window.
  const { isLoaded: clerkLoaded, isSignedIn: clerkSignedIn } = useUser();
  const tenantId = localStorage.getItem('tenant_id');
  const expiry = parseInt(localStorage.getItem('acp_token_expiry') || '0', 10);
  const legacyValid = !!tenantId && expiry > Date.now();
  const isValid = (clerkLoaded && clerkSignedIn) || legacyValid;
  const navigate = useNavigate();
  const verifiedRef = useRef(false);

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
    return <div className="min-h-screen bg-[#030303]" aria-hidden="true" />;
  }

  if (!isValid) {
    clearSessionMetadata();
    return <Navigate to="/login" replace />;
  }

  return <MainLayout>{children}</MainLayout>;
};

export default ProtectedRoute;
