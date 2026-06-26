import React from 'react'
import { Link } from 'react-router-dom'
import {
  Shield, Check, ArrowRight, Sparkles, Building2, Mail,
} from 'lucide-react'

// Sprint U11 — public Pricing page. Tier shape mirrors
// components/billing/PlanCard.jsx and services/gateway billing tiers so
// the marketing answer matches the runtime answer. No API calls — fully
// static so it renders for anonymous visitors.

const TIERS = [
  {
    id: 'free',
    name: 'Free',
    price: '$0',
    cadence: 'forever',
    blurb: 'For evaluating Aegis on a single agent. Shadow mode by default — nothing is blocked until you exit.',
    highlights: [
      'Up to 1,000 requests / day',
      '1 workspace · 1 employee key',
      '14-day shadow mode',
      'Verifiable audit log (Merkle + ed25519)',
      'Public transparency root',
    ],
    cta: { label: 'Start free', to: '/signup' },
  },
  {
    id: 'pro',
    name: 'Pro',
    price: '$499',
    cadence: 'per workspace / month',
    blurb: 'For teams running real production agents. Per-employee virtual keys, daily + monthly USD budgets, full policy engine.',
    badge: 'Most popular',
    accent: true,
    highlights: [
      'Up to 1M requests / day',
      'Unlimited employee virtual keys',
      'Per-employee USD budgets',
      'All 17 prompt-injection patterns',
      'All 5 compliance packs (SOC2 / PCI / HIPAA / Finance / DevOps)',
      'Slack / Jira / ServiceNow approval routing',
      'Email support — 1 business day',
    ],
    cta: { label: 'Start free trial', to: '/signup' },
  },
  {
    id: 'enterprise',
    name: 'Enterprise',
    price: 'Custom',
    cadence: 'annual',
    blurb: 'For regulated industries. Custom limits, SSO, SCIM, dedicated audit channel, on-prem option.',
    highlights: [
      'Unlimited requests',
      'SAML SSO + SCIM provisioning',
      'Dedicated SOC2 audit channel',
      'Bring-your-own-cloud (BYOC) deployment',
      'Custom signal packs + Rego policies',
      'Named CISO contact + SLA',
      'Slack-shared support · 4-hour P1 response',
    ],
    cta: { label: 'Contact sales', to: 'mailto:sales@aegisagent.in' },
  },
]

const FAQ = [
  {
    q: 'Do I need a credit card to start?',
    a: 'No. Free tier and 14-day Pro trial both onboard without a card.',
  },
  {
    q: 'How is "request" defined?',
    a: 'One Aegis decision — a tool call evaluated by the gateway or an LLM-proxy call routed through aegisagent.in/v1. Shadow-mode evaluations count the same as enforced ones.',
  },
  {
    q: 'Does Aegis see our prompts or LLM responses?',
    a: 'The gateway scans prompts for injection patterns and routes the request. We never store prompt bodies — only decision metadata + finding IDs land in the audit log.',
  },
  {
    q: 'Where is data hosted?',
    a: 'Pro is multi-tenant on AWS ap-south-1. Enterprise gets a dedicated region or full bring-your-own-cloud (BYOC) deployment in your VPC.',
  },
]


function TierCard({ tier }) {
  const isMail = tier.cta.to.startsWith('mailto:')
  const accent = tier.accent
  const ctaInner = (
    <>
      {tier.cta.label}
      <ArrowRight size={14} aria-hidden="true" />
    </>
  )
  return (
    <div
      className={`relative flex flex-col rounded-2xl border p-6 sm:p-7 ${
        accent
          ? 'border-white/20 bg-white/[0.04] shadow-[0_0_32px_rgba(255,255,255,0.04)]'
          : 'border-white/[0.07] bg-[#0a0a0a]'
      }`}
    >
      {tier.badge && (
        <span className="absolute -top-3 left-1/2 -translate-x-1/2 inline-flex items-center gap-1 px-3 py-1 rounded-full bg-white text-black text-[10px] font-bold uppercase tracking-widest">
          <Sparkles size={11} aria-hidden="true" />
          {tier.badge}
        </span>
      )}
      <div className="space-y-1">
        <h3 className="text-sm font-bold text-white uppercase tracking-widest">{tier.name}</h3>
        <div className="flex items-baseline gap-1.5">
          <span className="text-4xl font-bold text-white tracking-tight">{tier.price}</span>
          <span className="text-xs text-neutral-500">{tier.cadence}</span>
        </div>
        <p className="text-xs text-neutral-400 leading-relaxed pt-1">{tier.blurb}</p>
      </div>
      <ul className="space-y-2 mt-6 mb-8 flex-1">
        {tier.highlights.map((h) => (
          <li key={h} className="flex items-start gap-2 text-xs text-neutral-300 leading-snug">
            <Check size={13} className="text-white mt-0.5 shrink-0" aria-hidden="true" />
            <span>{h}</span>
          </li>
        ))}
      </ul>
      {isMail ? (
        <a
          href={tier.cta.to}
          className={`inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-sm font-semibold transition-colors ${
            accent ? 'bg-white text-black hover:bg-neutral-100' : 'border border-white/15 text-white hover:border-white/40 hover:bg-white/[0.04]'
          }`}
        >
          {ctaInner}
        </a>
      ) : (
        <Link
          to={tier.cta.to}
          className={`inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-sm font-semibold transition-colors ${
            accent ? 'bg-white text-black hover:bg-neutral-100' : 'border border-white/15 text-white hover:border-white/40 hover:bg-white/[0.04]'
          }`}
        >
          {ctaInner}
        </Link>
      )}
    </div>
  )
}


export default function Pricing() {
  return (
    <div className="min-h-screen bg-[#040404] text-neutral-100">
      <header className="border-b border-white/[0.06] px-4 sm:px-6 py-4">
        <div className="max-w-6xl mx-auto flex items-center justify-between gap-3">
          <Link to="/" className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-md bg-white text-black flex items-center justify-center">
              <Shield size={14} aria-hidden="true" />
            </div>
            <span className="text-sm font-bold text-white tracking-tight">Aegis</span>
          </Link>
          <div className="flex items-center gap-2">
            <Link to="/login" className="text-xs text-neutral-300 hover:text-white px-3 py-1.5 rounded-md transition-colors">
              Sign in
            </Link>
            <Link
              to="/signup"
              className="text-xs text-black bg-white px-3 py-1.5 rounded-md hover:bg-neutral-100 transition-colors font-semibold"
            >
              Start free
            </Link>
          </div>
        </div>
      </header>

      <section className="px-4 sm:px-6 py-16 sm:py-20 max-w-6xl mx-auto text-center">
        <div className="inline-flex items-center gap-2 text-[10px] uppercase tracking-widest text-neutral-500 mb-4">
          <Building2 size={11} aria-hidden="true" />
          <span>Pricing</span>
        </div>
        <h1 className="text-3xl sm:text-4xl lg:text-5xl font-bold tracking-tight text-white leading-tight">
          Govern every agent action.<br className="hidden sm:inline" /> Pay only for what you protect.
        </h1>
        <p className="text-sm lg:text-base text-neutral-400 leading-relaxed mt-5 max-w-2xl mx-auto">
          Free during evaluation. Per-workspace pricing for production.
          Enterprise terms for regulated industries.
        </p>
      </section>

      <section className="px-4 sm:px-6 pb-16 max-w-6xl mx-auto">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {TIERS.map((t) => <TierCard key={t.id} tier={t} />)}
        </div>
        <p className="text-[11px] text-neutral-500 text-center mt-6 max-w-2xl mx-auto">
          All tiers include the cryptographically verifiable audit chain — ed25519-signed
          Merkle roots mirrored to a public S3 bucket so any auditor can verify
          your evidence without trusting Aegis.
        </p>
      </section>

      <section className="px-4 sm:px-6 py-12 max-w-3xl mx-auto">
        <div className="text-[10px] uppercase tracking-widest text-neutral-500 mb-4 text-center">
          Common questions
        </div>
        <div className="space-y-3">
          {FAQ.map((item) => (
            <div key={item.q} className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-4">
              <div className="text-sm font-semibold text-white">{item.q}</div>
              <p className="text-xs text-neutral-400 leading-relaxed mt-1">{item.a}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="px-4 sm:px-6 py-12 max-w-3xl mx-auto text-center">
        <div className="rounded-2xl border border-white/[0.07] bg-[#0a0a0a] p-8 sm:p-10">
          <Mail size={20} className="mx-auto text-white mb-3" aria-hidden="true" />
          <h2 className="text-lg font-bold text-white">Not sure which tier fits?</h2>
          <p className="text-xs text-neutral-400 mt-2 max-w-md mx-auto">
            Tell us the volume, vendors, and compliance regime — we'll size it for you
            and quote in one email.
          </p>
          <a
            href="mailto:sales@aegisagent.in?subject=Aegis%20pricing%20question"
            className="inline-flex items-center gap-2 mt-5 px-4 py-2.5 rounded-lg bg-white text-black text-sm font-semibold hover:bg-neutral-100 transition-colors"
          >
            <Mail size={14} aria-hidden="true" />
            Email sales@aegisagent.in
          </a>
        </div>
      </section>

      <footer className="px-4 sm:px-6 py-10 mt-8 border-t border-white/[0.06] max-w-6xl mx-auto">
        <div className="flex items-center justify-between flex-wrap gap-3 text-[11px] text-neutral-500">
          <div className="flex items-center gap-2">
            <Shield size={12} aria-hidden="true" />
            <span className="text-neutral-300 font-semibold">Aegis</span>
            <span aria-hidden="true">·</span>
            <span>AI governance &amp; runtime security platform</span>
          </div>
          <div className="flex items-center gap-4">
            <Link to="/" className="hover:text-white">Home</Link>
            <Link to="/login" className="hover:text-white">Sign in</Link>
            <Link to="/signup" className="hover:text-white">Start free</Link>
          </div>
        </div>
      </footer>
    </div>
  )
}
