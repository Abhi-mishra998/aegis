import { useEffect, useRef } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import MainLayout from './MainLayout';
import { clearSessionMetadata, authService } from '../../services/api';
import { getSessionItem } from '../../lib/sessionStore';

// arch-26 W3.6 — role-ladder check mirroring backend
// services/gateway/_rbac_map.py ROLE_TIERS.
const ROLE_LADDER = ['ROOT', 'OWNER', 'ADMIN', 'SECURITY_ANALYST', 'DEVELOPER', 'READ_ONLY'];

function meetsRole(actual, required) {
  if (!required) return true;
  const a = (actual || '').toUpperCase();
  const r = required.toUpperCase();
  const ai = ROLE_LADDER.indexOf(a);
  const ri = ROLE_LADDER.indexOf(r);
  if (ai === -1 || ri === -1) return false;
  return ai <= ri;
}

const ProtectedRoute = ({ children, requiredRole = null }) => {
  const tenantId = getSessionItem('tenant_id');
  const expiry = parseInt(getSessionItem('acp_token_expiry') || '0', 10);
  const isValid = !!tenantId && expiry > Date.now();
  const navigate = useNavigate();
  const verifiedRef = useRef(false);

  // Once we render the protected page at least once, never swap back to the
  // "syncing" screen on subsequent state churn — background polls / SSE
  // reconnects would otherwise cause the page to blank mid-flow.
  const hasRenderedChildrenRef = useRef(false);

  useEffect(() => {
    // Demo sessions self-verify (identity-svc /auth/me rejects the demo JWT
    // even though every other endpoint accepts it). Skip the ping.
    if (getSessionItem('session_kind') === 'demo') return;
    if (!isValid || verifiedRef.current) return;
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
  }, [isValid, navigate]);

  if (!isValid && !hasRenderedChildrenRef.current) {
    clearSessionMetadata();
    return <Navigate to="/login" replace />;
  }

  hasRenderedChildrenRef.current = true;

  const userRole = getSessionItem('role') || getSessionItem('user_role') || 'READ_ONLY';
  if (requiredRole && !meetsRole(userRole, requiredRole)) {
    return (
      <MainLayout>
        <div className="min-h-[60vh] flex items-center justify-center px-4" role="alert">
          <div className="max-w-md text-center space-y-3">
            <div className="text-xs uppercase tracking-widest text-neutral-500">403 — Access denied</div>
            <h1 className="text-2xl font-bold text-white">You don't have access to this page</h1>
            <p className="text-sm text-neutral-400 leading-relaxed">
              This view requires <span className="font-semibold text-white">{requiredRole}</span> role or higher.
              Your role is <span className="font-mono text-neutral-300">{userRole}</span>. Ask your
              workspace OWNER to upgrade your role in Settings → User Management.
            </p>
          </div>
        </div>
      </MainLayout>
    );
  }

  return <MainLayout>{children}</MainLayout>;
};

export default ProtectedRoute;
