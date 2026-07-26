import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Shield, Loader2 } from 'lucide-react';
import { requestPasswordReset } from '../services/auth';

export default function ForgotPassword() {
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState(null);

  const onSubmit = async (e) => {
    e.preventDefault();
    if (busy) return;
    setError(null);
    setBusy(true);
    try {
      await requestPasswordReset(email);
      setSent(true);
    } catch (err) {
      setError(err.message || 'Request failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#030303] flex flex-col items-center justify-center px-4 py-8">
      <Link
        to="/login"
        className="absolute top-4 left-4 z-20 inline-flex items-center gap-1.5 text-[11px] text-neutral-500 hover:text-neutral-200 transition-colors"
      >
        <span aria-hidden="true">←</span>
        <span>Back to sign in</span>
      </Link>

      <div className="w-full max-w-sm">
        <div className="flex flex-col items-center gap-4 mb-6">
          <div className="w-12 h-12 rounded-xl bg-white flex items-center justify-center">
            <Shield size={24} className="text-black" aria-hidden="true" />
          </div>
          <div className="text-center space-y-1.5">
            <h1 className="text-2xl font-bold text-white">Reset your password</h1>
            <p className="text-xs text-neutral-400 max-w-[280px] mx-auto">
              We'll send a one-time link if that email is registered. Link expires in 15 minutes.
            </p>
          </div>
        </div>

        {sent ? (
          <div className="bg-[#0a0a0a] border border-white/[0.07] rounded-2xl p-6 space-y-4">
            <p className="text-sm text-neutral-300">
              If <span className="font-mono text-white">{email}</span> is registered, a reset link has been issued.
            </p>
            <p className="text-xs text-neutral-500 leading-relaxed">
              Self-hosted: the token is logged to the identity service (grep for <span className="font-mono">password_reset_requested</span>).
              Hosted: check your inbox. Follow the link within 15 minutes.
            </p>
            <Link to="/login" className="block text-center text-xs text-white underline pt-2">
              Back to sign in
            </Link>
          </div>
        ) : (
          <form
            onSubmit={onSubmit}
            className="bg-[#0a0a0a] border border-white/[0.07] rounded-2xl p-6 space-y-4"
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

            {error && <p className="text-xs text-red-400" role="alert">{error}</p>}

            <button
              type="submit"
              disabled={busy || !email}
              className="w-full bg-white text-black font-semibold rounded-lg py-2 text-sm disabled:opacity-60 disabled:cursor-not-allowed flex items-center justify-center gap-2"
            >
              {busy && <Loader2 size={14} className="animate-spin" aria-hidden="true" />}
              {busy ? 'Sending…' : 'Send reset link'}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
