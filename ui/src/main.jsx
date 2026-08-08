import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './index.css';

// ponytail: demo flow now requires POST /auth/demo-token/exchange server round-trip — client cannot forge sessions
// Previously an IIFE here (see C7 in security audit 2026-07-31) base64-decoded
// any URL-supplied `?demo_token=<jwt>` with NO signature check and installed it
// as an authenticated cookie + sessionStorage session. A phishing link
// `aegisagent.in/dashboard?demo_token=<anything>` rendered the victim's tab as
// an OWNER of any tenant the attacker named. Deleted. Any future demo flow
// must POST /auth/demo-token/exchange server-side (endpoint does not exist
// yet — coordinate with the identity team) and receive an httpOnly Set-Cookie
// back on the response.

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
