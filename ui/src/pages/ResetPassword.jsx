import { useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { Shield, Loader2 } from 'lucide-react';
import { confirmPasswordReset } from '../services/auth';

export default function ResetPassword() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const initialToken = params.get('token') || '';
  const [token, setToken] = useState(initialToken);
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    // Strip token from URL so it isn't retained in browser history.
    if (initialToken) {
      const clean = window.location.pathname + window.location.hash;
      window.history.replaceState({}, '', clean);
    }
  }, [initialToken]);

  const onSubmit = async (e) => {
    e.preventDefault();
    if (busy) return;
    setError(null);
    if (password.length < 8) return setError('Password must be at least 8 characters.');
    if (password !== confirm) return setError('Passwords do not match.');
    setBusy(true);
    try {
      await confirmPasswordReset({ token, new_password: password });
      setDone(true);
      setTimeout(() => navigate('/dashboard', { replace: true }), 800);
    } catch (err) {
      setError(err.message || 'Reset failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#030303] flex flex-col items-center justify-center px-4 py-8">
      <div className="w-full max-w-sm">
        <div className="flex flex-col items-center gap-4 mb-6">
          <div className="w-12 h-12 rounded-xl bg-white flex items-center justify-center">
            <Shield size={24} className="text-black" aria-hidden="true" />
          </div>
          <div className="text-center space-y-1.5">
            <h1 className="text-2xl font-bold text-white">Set a new password</h1>
          </div>
        </div>

        {done ? (
          <div className="bg-[#0a0a0a] border border-white/[0.07] rounded-2xl p-6 text-center space-y-3">
            <p className="text-sm text-neutral-300">Password updated. Signing you in…</p>
          </div>
        ) : (
          <form
            onSubmit={onSubmit}
            className="bg-[#0a0a0a] border border-white/[0.07] rounded-2xl p-6 space-y-4"
          >
            {!initialToken && (
              <label className="block">
                <span className="text-[11px] uppercase tracking-widest text-neutral-500">Reset token</span>
                <input
                  type="text"
                  required
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  className="mt-1 w-full font-mono text-xs bg-[#0a0a0a] border border-white/[0.07] focus:border-white/30 rounded-lg px-3 py-2 text-white outline-none"
                />
              </label>
            )}

            <label className="block">
              <span className="text-[11px] uppercase tracking-widest text-neutral-500">New password</span>
              <input
                type="password"
                required
                autoComplete="new-password"
                minLength={8}
                autoFocus
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="mt-1 w-full bg-[#0a0a0a] border border-white/[0.07] focus:border-white/30 rounded-lg px-3 py-2 text-sm text-white outline-none"
              />
            </label>

            <label className="block">
              <span className="text-[11px] uppercase tracking-widest text-neutral-500">Confirm password</span>
              <input
                type="password"
                required
                autoComplete="new-password"
                minLength={8}
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                className="mt-1 w-full bg-[#0a0a0a] border border-white/[0.07] focus:border-white/30 rounded-lg px-3 py-2 text-sm text-white outline-none"
              />
            </label>

            {error && <p className="text-xs text-red-400" role="alert">{error}</p>}

            <button
              type="submit"
              disabled={busy || !token}
              className="w-full bg-white text-black font-semibold rounded-lg py-2 text-sm disabled:opacity-60 disabled:cursor-not-allowed flex items-center justify-center gap-2"
            >
              {busy && <Loader2 size={14} className="animate-spin" aria-hidden="true" />}
              {busy ? 'Updating…' : 'Update password'}
            </button>

            <Link to="/login" className="block text-center text-[11px] text-neutral-500 hover:text-white transition-colors">
              Back to sign in
            </Link>
          </form>
        )}
      </div>
    </div>
  );
}
