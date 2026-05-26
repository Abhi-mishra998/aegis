import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, setSessionMetadata } from '../services/api';
import { Shield, AlertCircle, Eye, EyeOff, Lock, Zap } from 'lucide-react';
import Button from '../components/Common/Button';
import { useAuth } from '../hooks/useAuth';

const GW = typeof window !== 'undefined'
  ? window.location.origin
  : (import.meta?.env?.VITE_API_BASE_URL || 'https://aegisagent.in');

export default function Login() {
  const navigate = useNavigate();
  const { updateAuth } = useAuth();

  const [email, setEmail]           = useState('');
  const [password, setPassword]     = useState('');
  const [showPw, setShowPw]         = useState(false);
  const [error, setError]           = useState('');
  const [loading, setLoading]       = useState(false);
  const [demoLoading, setDemoLoading] = useState(false);
  const [ssoProviders, setSsoProviders] = useState([]);

  useEffect(() => {
    // Detect SSO error from callback redirect
    const params = new URLSearchParams(window.location.search);
    const ssoErr = params.get('sso_error');
    if (ssoErr) setError(`SSO login failed: ${ssoErr.replace(/_/g, ' ')}`);

    // Load enabled SSO providers
    api.getSSOProviders()
      .then(res => setSsoProviders(res?.providers || []))
      .catch(() => {});
  }, []);

  const doLogin = async (loginEmail, loginPassword, { isDemo = false } = {}) => {
    const setL = isDemo ? setDemoLoading : setLoading;
    setL(true);
    setError('');
    try {
      const res = await api.login({ email: loginEmail, password: loginPassword });
      const tenantId  = res?.data?.tenant_id;
      const expiresIn = res?.data?.expires_in;

      if (!tenantId) {
        setError(isDemo
          ? 'Demo account not ready. Run scripts/seed_demo_data.py first.'
          : 'Invalid credentials. Please try again.');
        return;
      }

      const role = res?.data?.role;
      setSessionMetadata({ tenant_id: tenantId, expires_in: expiresIn, user_email: loginEmail, role });
      updateAuth({ isAuthenticated: true, user: loginEmail, tenant_id: tenantId, role: role || null, token: null });
      navigate('/flight-recorder', { replace: true });
    } catch (err) {
      const msg = err?.response?.data?.detail || err?.message;
      setError(isDemo
        ? 'Demo login failed — account may not exist yet.'
        : (msg || 'Authentication failed. Please try again.'));
    } finally {
      setL(false);
    }
  };

  const handleLogin = (e) => {
    e.preventDefault();
    if (!email || !password) { setError('Please enter your email and password.'); return; }
    doLogin(email, password);
  };

  const handleDemoLogin = () => doLogin('demo@aegisagent.in', 'demo1234', { isDemo: true });

  return (
    <div className="min-h-screen bg-[#030303] flex flex-col items-center justify-center px-4 relative overflow-hidden">
      {/* Background texture */}
      <div className="absolute inset-0 grid-baseline opacity-[0.06] pointer-events-none" aria-hidden="true" />
      <div className="absolute top-0 left-0 w-full h-px bg-gradient-to-r from-transparent via-white/10 to-transparent" aria-hidden="true" />

      <div className="w-full max-w-sm relative z-10 animate-scale-in">
        {/* Logo */}
        <div className="flex flex-col items-center gap-4 mb-8">
          <div className="w-12 h-12 rounded-xl bg-white flex items-center justify-center shadow-[0_0_24px_rgba(255,255,255,0.15)]">
            <Shield size={24} className="text-black" aria-hidden="true" />
          </div>
          <div className="text-center space-y-1.5">
            <h1 className="text-2xl font-bold tracking-tight text-white">AgentControl</h1>
            <p className="text-xs text-neutral-400 leading-relaxed max-w-[260px] mx-auto">
              Tamper-evident replay + runtime deny for AI agents.
            </p>
          </div>
        </div>

        {/* Form card */}
        <div className="bg-[#0a0a0a] border border-white/[0.07] rounded-2xl p-8 shadow-2xl space-y-5">
          {/* Error alert */}
          {error && (
            <div role="alert" className="flex items-start gap-3 p-3.5 rounded-xl bg-red-500/[0.08] border border-red-500/20 animate-scale-in">
              <AlertCircle className="text-red-400 shrink-0 mt-0.5" size={15} aria-hidden="true" />
              <p className="text-xs text-red-400 leading-snug">{error}</p>
            </div>
          )}

          <form className="space-y-4" onSubmit={handleLogin} noValidate>
            {/* Email */}
            <div className="space-y-1.5">
              <label htmlFor="email" className="label-standard">Email</label>
              <input
                id="email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@company.com"
                className="input-standard h-11"
              />
            </div>

            {/* Password */}
            <div className="space-y-1.5">
              <label htmlFor="password" className="label-standard">Password</label>
              <div className="relative">
                <input
                  id="password"
                  type={showPw ? 'text' : 'password'}
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  className="input-standard h-11 pr-10"
                />
                <button
                  type="button"
                  onClick={() => setShowPw((v) => !v)}
                  aria-label={showPw ? 'Hide password' : 'Show password'}
                  className="absolute right-3 top-1/2 -translate-y-1/2 p-1 text-neutral-600 hover:text-neutral-400 transition-colors rounded"
                >
                  {showPw ? <EyeOff size={15} aria-hidden="true" /> : <Eye size={15} aria-hidden="true" />}
                </button>
              </div>
            </div>

            {/* Submit */}
            <div className="pt-2">
              <Button type="submit" loading={loading} disabled={loading || demoLoading} className="w-full h-11 text-sm font-semibold">
                Sign In
              </Button>
            </div>
          </form>

          {/* Divider */}
          <div className="flex items-center gap-3">
            <div className="flex-1 h-px bg-white/[0.06]" />
            <span className="text-[10px] text-neutral-700 uppercase tracking-widest">or</span>
            <div className="flex-1 h-px bg-white/[0.06]" />
          </div>

          {/* SSO buttons — rendered only when providers are configured */}
          {ssoProviders.length > 0 && (
            <div className="space-y-2">
              {ssoProviders.includes('google') && (
                <a
                  href={`${GW}/auth/sso/google`}
                  className="w-full flex items-center justify-center gap-2 h-11 rounded-xl border border-white/10 bg-white/[0.04] text-white/80 text-sm font-medium hover:bg-white/[0.08] hover:border-white/20 transition-all duration-200"
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">
                    <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
                    <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
                    <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z"/>
                    <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
                  </svg>
                  Continue with Google
                </a>
              )}
              {ssoProviders.includes('microsoft') && (
                <a
                  href={`${GW}/auth/sso/microsoft`}
                  className="w-full flex items-center justify-center gap-2 h-11 rounded-xl border border-white/10 bg-white/[0.04] text-white/80 text-sm font-medium hover:bg-white/[0.08] hover:border-white/20 transition-all duration-200"
                >
                  <svg width="16" height="16" viewBox="0 0 21 21" aria-hidden="true">
                    <rect x="1" y="1" width="9" height="9" fill="#f25022"/>
                    <rect x="11" y="1" width="9" height="9" fill="#7fba00"/>
                    <rect x="1" y="11" width="9" height="9" fill="#00a4ef"/>
                    <rect x="11" y="11" width="9" height="9" fill="#ffb900"/>
                  </svg>
                  Continue with Microsoft
                </a>
              )}
              {ssoProviders.includes('okta') && (
                <a
                  href={`${GW}/auth/sso/okta`}
                  className="w-full flex items-center justify-center gap-2 h-11 rounded-xl border border-white/10 bg-white/[0.04] text-white/80 text-sm font-medium hover:bg-white/[0.08] hover:border-white/20 transition-all duration-200"
                >
                  <svg width="16" height="16" viewBox="0 0 50 50" fill="#007DC1" aria-hidden="true">
                    <path d="M25 0C11.2 0 0 11.2 0 25s11.2 25 25 25 25-11.2 25-25S38.8 0 25 0zm0 37.5c-6.9 0-12.5-5.6-12.5-12.5S18.1 12.5 25 12.5 37.5 18.1 37.5 25 31.9 37.5 25 37.5z"/>
                  </svg>
                  Continue with Okta
                </a>
              )}
              <div className="flex items-center gap-3 pt-1">
                <div className="flex-1 h-px bg-white/[0.06]" />
                <span className="text-[10px] text-neutral-700 uppercase tracking-widest">or</span>
                <div className="flex-1 h-px bg-white/[0.06]" />
              </div>
            </div>
          )}

          {/* Demo login */}
          <button
            type="button"
            onClick={handleDemoLogin}
            disabled={loading || demoLoading}
            className="w-full flex items-center justify-center gap-2 h-11 rounded-xl border border-blue-500/30 bg-blue-500/[0.06] text-blue-400 text-sm font-semibold hover:bg-blue-500/[0.10] hover:border-blue-500/50 transition-all duration-200 disabled:opacity-40"
          >
            {demoLoading
              ? <div className="w-4 h-4 border-2 border-blue-400/30 border-t-blue-400 rounded-full animate-spin" />
              : <Zap size={15} aria-hidden="true" />}
            Try Live Demo
          </button>

          {/* Footer note */}
          <div className="flex items-center justify-center gap-2 pt-1">
            <Lock size={11} className="text-neutral-700" aria-hidden="true" />
            <p className="text-xs text-neutral-700">Encrypted · Authorized Personnel Only</p>
          </div>
        </div>

        {/* Demo hint */}
        <p className="text-center text-[10px] text-neutral-800 mt-4">
          Demo: demo@aegisagent.in / demo1234
        </p>
      </div>
    </div>
  );
}
