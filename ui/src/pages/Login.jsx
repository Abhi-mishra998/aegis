import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, setSessionMetadata } from '../services/api';
import { Shield, AlertCircle, Eye, EyeOff, Lock, Zap } from 'lucide-react';
import Button from '../components/Common/Button';
import { useAuth } from '../hooks/useAuth';

export default function Login() {
  const navigate = useNavigate();
  const { updateAuth } = useAuth();

  const [email, setEmail]       = useState('');
  const [password, setPassword] = useState('');
  const [showPw, setShowPw]     = useState(false);
  const [error, setError]       = useState('');
  const [loading, setLoading]   = useState(false);
  const [demoLoading, setDemoLoading] = useState(false);

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

  const handleDemoLogin = () => doLogin('demo@aegisagent.in', 'demo', { isDemo: true });

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
          Demo: demo@aegisagent.in / demo
        </p>
      </div>
    </div>
  );
}
