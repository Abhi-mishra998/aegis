import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Shield, Lock, Loader2 } from 'lucide-react';
import { register } from '../services/auth';

export default function Signup() {
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [workspaceName, setWorkspaceName] = useState('');
  const [fullName, setFullName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const onSubmit = async (e) => {
    e.preventDefault();
    if (busy) return;
    setError(null);
    if (password.length < 8) {
      setError('Password must be at least 8 characters.');
      return;
    }
    setBusy(true);
    try {
      await register({
        email,
        password,
        workspace_name: workspaceName || undefined,
        full_name: fullName || undefined,
      });
      navigate('/dashboard', { replace: true });
    } catch (err) {
      setError(err.message || 'Sign-up failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#030303] flex flex-col items-center justify-center px-4 py-8 sm:py-10 relative overflow-hidden">
      <div className="absolute inset-0 grid-baseline opacity-[0.06] pointer-events-none" aria-hidden="true" />
      <div className="absolute top-0 left-0 w-full h-px bg-gradient-to-r from-transparent via-white/10 to-transparent" aria-hidden="true" />

      <Link
        to="/"
        className="absolute top-4 left-4 sm:top-6 sm:left-6 z-20 inline-flex items-center gap-1.5 text-[11px] text-neutral-500 hover:text-neutral-200 transition-colors"
      >
        <span aria-hidden="true">←</span>
        <span>Home</span>
      </Link>

      <div className="w-full max-w-sm relative z-10 animate-scale-in">
        <div className="flex flex-col items-center gap-4 mb-6">
          <div className="w-12 h-12 rounded-xl bg-white flex items-center justify-center shadow-[0_0_24px_rgba(255,255,255,0.15)]">
            <Shield size={24} className="text-black" aria-hidden="true" />
          </div>
          <div className="text-center space-y-1.5">
            <h1 className="text-2xl font-bold tracking-tight text-white">Govern your AI agents</h1>
            <p className="text-xs text-neutral-400 leading-relaxed max-w-[300px] mx-auto">
              Allow, deny, escalate or require approval on every agent action. 14-day shadow mode by default — no production breakage. Block when you're ready.
            </p>
          </div>
        </div>

        <form
          onSubmit={onSubmit}
          className="bg-[#0a0a0a] border border-white/[0.07] rounded-2xl shadow-2xl p-6 space-y-4"
          aria-label="Create account"
        >
          <label className="block">
            <span className="text-[11px] uppercase tracking-widest text-neutral-500">Email</span>
            <input
              type="email"
              required
              autoComplete="email"
              autoFocus
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mt-1 w-full bg-[#0a0a0a] border border-white/[0.07] focus:border-white/30 rounded-lg px-3 py-2 text-sm text-white outline-none"
            />
          </label>

          <label className="block">
            <span className="text-[11px] uppercase tracking-widest text-neutral-500">Password</span>
            <input
              type="password"
              required
              autoComplete="new-password"
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1 w-full bg-[#0a0a0a] border border-white/[0.07] focus:border-white/30 rounded-lg px-3 py-2 text-sm text-white outline-none"
            />
            <span className="text-[10px] text-neutral-600 mt-1 block">Minimum 8 characters.</span>
          </label>

          <label className="block">
            <span className="text-[11px] uppercase tracking-widest text-neutral-500">Workspace name <span className="text-neutral-600">(optional)</span></span>
            <input
              type="text"
              value={workspaceName}
              onChange={(e) => setWorkspaceName(e.target.value)}
              placeholder="Acme AI Ops"
              className="mt-1 w-full bg-[#0a0a0a] border border-white/[0.07] focus:border-white/30 rounded-lg px-3 py-2 text-sm text-white outline-none placeholder:text-neutral-700"
            />
          </label>

          <label className="block">
            <span className="text-[11px] uppercase tracking-widest text-neutral-500">Full name <span className="text-neutral-600">(optional)</span></span>
            <input
              type="text"
              autoComplete="name"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              className="mt-1 w-full bg-[#0a0a0a] border border-white/[0.07] focus:border-white/30 rounded-lg px-3 py-2 text-sm text-white outline-none"
            />
          </label>

          {error && (
            <p className="text-xs text-red-400 leading-relaxed" role="alert">{error}</p>
          )}

          <button
            type="submit"
            disabled={busy}
            className="w-full bg-white text-black font-semibold rounded-lg py-2 text-sm disabled:opacity-60 disabled:cursor-not-allowed flex items-center justify-center gap-2"
          >
            {busy && <Loader2 size={14} className="animate-spin" aria-hidden="true" />}
            {busy ? 'Creating workspace…' : 'Create account'}
          </button>
        </form>

        <div className="flex items-center justify-center gap-2 mt-5">
          <Lock size={11} className="text-neutral-700" aria-hidden="true" />
          <p className="text-xs text-neutral-700">14-day shadow mode · Tamper-evident audit · ed25519 receipts</p>
        </div>

        <p className="text-center text-[11px] text-neutral-500 mt-3">
          Already have an account?{' '}
          <Link to="/login" className="text-white hover:underline">Sign in</Link>
        </p>
      </div>
    </div>
  );
}
