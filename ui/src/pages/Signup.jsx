import React from 'react';
import { SignUp } from '@clerk/react';
import { Link } from 'react-router-dom';
import { Shield, Lock } from 'lucide-react';

const aegisAppearance = {
  variables: {
    colorPrimary: '#ffffff',
    colorBackground: '#0a0a0a',
    colorInputBackground: '#0a0a0a',
    colorInputText: '#ffffff',
    colorText: '#ffffff',
    colorTextSecondary: '#a3a3a3',
    colorDanger: '#f87171',
    borderRadius: '0.75rem',
    fontFamily: 'inherit',
    fontSize: '0.875rem',
  },
  elements: {
    rootBox: 'w-full',
    card: 'bg-[#0a0a0a] border border-white/[0.07] rounded-2xl shadow-2xl',
    headerTitle: 'text-white',
    headerSubtitle: 'text-neutral-400',
    formFieldLabel: 'text-neutral-300',
    formFieldInput:
      'bg-[#0a0a0a] border-white/[0.07] text-white focus:border-white/30',
    formButtonPrimary:
      'bg-white text-black hover:bg-neutral-200 normal-case font-semibold',
    footerActionLink: 'text-white hover:underline',
    identityPreviewText: 'text-neutral-300',
    identityPreviewEditButton: 'text-white',
    socialButtonsBlockButton:
      'border border-white/10 hover:bg-white/[0.08] text-white',
    socialButtonsBlockButtonText: 'text-white',
    dividerLine: 'bg-white/[0.06]',
    dividerText: 'text-neutral-700',
    formFieldErrorText: 'text-red-400',
    alertText: 'text-red-400',
  },
};

export default function Signup() {
  return (
    <div className="min-h-screen bg-[#030303] flex flex-col items-center justify-center px-4 py-10 relative overflow-hidden">
      <div
        className="absolute inset-0 grid-baseline opacity-[0.06] pointer-events-none"
        aria-hidden="true"
      />
      <div
        className="absolute top-0 left-0 w-full h-px bg-gradient-to-r from-transparent via-white/10 to-transparent"
        aria-hidden="true"
      />

      <div className="w-full max-w-sm relative z-10 animate-scale-in">
        <div className="flex flex-col items-center gap-4 mb-6">
          <div className="w-12 h-12 rounded-xl bg-white flex items-center justify-center shadow-[0_0_24px_rgba(255,255,255,0.15)]">
            <Shield size={24} className="text-black" aria-hidden="true" />
          </div>
          <div className="text-center space-y-1.5">
            <h1 className="text-2xl font-bold tracking-tight text-white">
              Create your workspace
            </h1>
            <p className="text-xs text-neutral-400 leading-relaxed max-w-[280px] mx-auto">
              Aegis protects your AI agents in shadow mode for the first 14
              days. No production breakage. Block when you're ready.
            </p>
          </div>
        </div>

        <SignUp
          path="/signup"
          routing="path"
          signInUrl="/login"
          afterSignUpUrl="/flight-recorder"
          appearance={aegisAppearance}
        />

        <div className="flex items-center justify-center gap-2 mt-5">
          <Lock size={11} className="text-neutral-700" aria-hidden="true" />
          <p className="text-xs text-neutral-700">
            14-day shadow mode · Tamper-evident audit · ed25519 receipts
          </p>
        </div>

        <p className="text-center text-[11px] text-neutral-500 mt-3">
          Already have an account?{' '}
          <Link to="/login" className="text-white hover:underline">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
