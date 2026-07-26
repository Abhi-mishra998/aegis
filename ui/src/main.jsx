import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './index.css';

// Demo tenant bridge — the /demo/spawn-workspace flow redirects the
// browser here with ?demo_token=<jwt>. We install the cookie + session
// metadata BEFORE React renders so ProtectedRoute doesn't bounce to
// /login on the first paint. Mirrored in components/Layout/DemoTokenBridge.jsx
// for the client-side-nav case.
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
    sessionStorage.setItem('session_kind', 'demo');

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

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
