import React from 'react';
import ReactDOM from 'react-dom/client';
import { ClerkProvider } from '@clerk/react';
import App from './App';
import './index.css';

// /demo/spawn-workspace redirect lands on /dashboard?demo_token=… The SPA
// must install session state BEFORE React renders or ProtectedRoute runs
// its synchronous redirect-to-login on the first paint and the bridge's
// useEffect (which runs after that) sees a URL with no token. We install
// the metadata + cookie here in main.jsx so the session exists before
// ReactDOM.createRoot.render is called.
//
// Mirrored async version in components/Layout/DemoTokenBridge.jsx handles
// the case where the param appears via client-side nav after boot.
(function consumeDemoTokenOnBoot() {
  try {
    const params = new URLSearchParams(window.location.search);
    const demoToken = params.get('demo_token');
    if (!demoToken) return;

    const parts = demoToken.split('.');
    if (parts.length !== 3) return;
    let claims;
    try {
      const b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
      claims = JSON.parse(atob(b64));
    } catch { return; }

    const nowSec = Math.floor(Date.now() / 1000);
    const expSec = Number(claims.exp || 0);
    if (!expSec || expSec <= nowSec) return;
    const ttlSeconds = expSec - nowSec;

    const tenantId = claims.tenant_id || claims.aegis_tenant_id;
    const email = claims.sub || claims.email || claims.user_email || 'demo';
    const role = String(claims.role || claims.aegis_role || 'OWNER').toUpperCase();
    if (!tenantId) return;

    const isSecure = window.location.protocol === 'https:';
    const cookieAttrs = [
      `acp_token=${demoToken}`,
      'path=/',
      `max-age=${ttlSeconds}`,
      'samesite=Strict',
    ];
    if (isSecure) cookieAttrs.push('Secure');
    document.cookie = cookieAttrs.join('; ');

    sessionStorage.setItem('tenant_id', tenantId);
    sessionStorage.setItem('user_email', email);
    sessionStorage.setItem('user_role', role);
    sessionStorage.setItem('acp_token_expiry', String(Date.now() + ttlSeconds * 1000));

    params.delete('demo_token');
    const cleanUrl =
      window.location.pathname +
      (params.toString() ? `?${params.toString()}` : '') +
      window.location.hash;
    window.history.replaceState({}, '', cleanUrl);
  } catch {
    // Silent fail; DemoTokenBridge component will retry on mount.
  }
})();

const CLERK_PUBLISHABLE_KEY = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY;

if (!CLERK_PUBLISHABLE_KEY) {
  throw new Error(
    'VITE_CLERK_PUBLISHABLE_KEY is missing. Set it in ui/.env (or .env.local) before starting the dev server.',
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ClerkProvider
      publishableKey={CLERK_PUBLISHABLE_KEY}
      signInUrl="/login"
      signUpUrl="/signup"
      afterSignInUrl="/dashboard"
      afterSignUpUrl="/dashboard"
      afterSignOutUrl="/login"
    >
      <App />
    </ClerkProvider>
  </React.StrictMode>,
);
