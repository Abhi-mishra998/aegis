import React, { useState } from 'react'
import { CheckCircle2, Zap, Shield, Building2, ArrowRight, Star } from 'lucide-react'

const PLANS = [
  {
    id:        'starter',
    name:      'Starter',
    icon:      Zap,
    price:     { monthly: 299, annual: 249 },
    desc:      'For teams building their first AI governance layer',
    color:     'blue',
    highlight: false,
    limits: {
      executions:  '50K / month',
      agents:      '5 agents',
      tenants:     '1 tenant',
      sso:         false,
      audit:       '30-day retention',
      support:     'Community',
      sdk:         true,
      policyTests: '100 / month',
      sla:         '99.5%',
    },
    features: [
      'Visual Policy Builder',
      'OPA-backed enforcement',
      'Tamper-evident audit log',
      'Risk scoring (5 classifiers)',
      'LangChain + OpenAI SDKs',
      'Basic Slack alerts',
    ],
  },
  {
    id:        'professional',
    name:      'Professional',
    icon:      Shield,
    price:     { monthly: 999, annual: 829 },
    desc:      'For production AI workloads with compliance requirements',
    color:     'purple',
    highlight: true,
    limits: {
      executions:  '500K / month',
      agents:      '50 agents',
      tenants:     '5 tenants',
      sso:         true,
      audit:       '1-year retention + PDF export',
      support:     'Email (48h SLA)',
      sdk:         true,
      policyTests: 'Unlimited',
      sla:         '99.9%',
    },
    features: [
      'Everything in Starter',
      'SSO / OIDC (Google, Microsoft, Okta)',
      'EU AI Act PDF compliance reports',
      'NIST AI RMF + SOC 2 evidence bundles',
      'Playbooks engine (auto-remediation)',
      'Flight Recorder (full replay)',
      'Cryptographic audit chain',
      'Identity Graph + blast-radius analysis',
      'Anthropic SDK integration',
      'Cost caps + quota management',
    ],
  },
  {
    id:        'enterprise',
    name:      'Enterprise',
    icon:      Building2,
    price:     null,
    desc:      'Custom pricing for regulated industries and large deployments',
    color:     'amber',
    highlight: false,
    limits: {
      executions:  'Unlimited',
      agents:      'Unlimited',
      tenants:     'Unlimited',
      sso:         true,
      audit:       'Unlimited + WORM storage',
      support:     'Dedicated CSM + Slack Connect',
      sdk:         true,
      policyTests: 'Unlimited',
      sla:         '99.99%',
    },
    features: [
      'Everything in Professional',
      'AWS WAF integration',
      'Custom OPA policy deployment',
      'Air-gapped / VPC deployment',
      'SAML 2.0 + custom IdP',
      'Auto-remediation playbooks with webhooks',
      'SOC 2 Type II report on request',
      'Custom retention + WORM audit store',
      'Dedicated infrastructure',
      'SLA with financial penalties',
    ],
  },
]

const COLOR = {
  blue:   {
    icon:   'text-blue-400 bg-blue-500/10',
    badge:  'text-blue-400 bg-blue-500/10 border-blue-500/20',
    btn:    'bg-blue-600 hover:bg-blue-500 text-white',
    border: 'border-blue-500/20',
    ring:   '',
  },
  purple: {
    icon:   'text-purple-400 bg-purple-500/10',
    badge:  'text-purple-400 bg-purple-500/10 border-purple-500/20',
    btn:    'bg-purple-600 hover:bg-purple-500 text-white',
    border: 'border-purple-500/40',
    ring:   'ring-1 ring-purple-500/30',
  },
  amber:  {
    icon:   'text-amber-400 bg-amber-500/10',
    badge:  'text-amber-400 bg-amber-500/10 border-amber-500/20',
    btn:    'bg-amber-600 hover:bg-amber-500 text-white',
    border: 'border-amber-500/20',
    ring:   '',
  },
}

const LIMIT_LABELS = [
  ['executions',  'Executions'],
  ['agents',      'Agents'],
  ['tenants',     'Tenants'],
  ['sso',         'SSO / OIDC'],
  ['audit',       'Audit retention'],
  ['policyTests', 'Policy tests'],
  ['sla',         'Uptime SLA'],
  ['support',     'Support'],
]

const FAQS = [
  {
    q: 'What counts as an execution?',
    a: 'One execution = one call to POST /execute — i.e., one tool call from an AI agent that passes through the Aegis enforcement engine. Cached allow decisions do not count.',
  },
  {
    q: 'Can I change plans at any time?',
    a: 'Yes. Upgrades are prorated and take effect immediately. Downgrades apply at the next billing cycle.',
  },
  {
    q: 'Is there a free trial?',
    a: 'All plans come with a 14-day free trial. No credit card required. Full Starter-tier access during the trial.',
  },
  {
    q: 'Do you offer discounts for startups or non-profits?',
    a: 'Yes — 50% off Starter and Professional for YC-backed startups and registered non-profits. Contact sales.',
  },
  {
    q: 'Is my audit data portable?',
    a: 'Always. Export your full tenant audit archive (JSON + chain proofs) at any time via the Compliance Export page or the API. Enterprise customers can configure direct S3/GCS egress.',
  },
  {
    q: 'How does Enterprise pricing work?',
    a: 'Enterprise is quoted based on execution volume, number of tenants, infrastructure requirements, and SLA tier. Typical contracts start at $3,500/month.',
  },
]

export default function Pricing() {
  const [annual, setAnnual] = useState(true)

  return (
    <div className="space-y-10 animate-fade-in max-w-6xl mx-auto pb-12">
      {/* Header */}
      <div className="text-center pt-4 space-y-3">
        <div className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[11px] text-purple-400 bg-purple-500/10 border border-purple-500/20 mb-2">
          <Star size={11} /> Trusted by AI teams shipping production agents
        </div>
        <h1 className="text-3xl font-bold text-white tracking-tight">Simple, transparent pricing</h1>
        <p className="text-sm text-neutral-400 max-w-lg mx-auto">
          Govern every AI agent call — without slowing them down. Start free, scale to enterprise.
        </p>

        {/* Billing toggle */}
        <div className="flex items-center justify-center gap-3 pt-2">
          <span className={`text-xs ${!annual ? 'text-white' : 'text-neutral-500'}`}>Monthly</span>
          <button
            onClick={() => setAnnual(v => !v)}
            className={`relative w-10 h-5 rounded-full transition-colors ${annual ? 'bg-purple-600' : 'bg-white/10'}`}
          >
            <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${annual ? 'translate-x-5' : 'translate-x-0.5'}`} />
          </button>
          <span className={`text-xs ${annual ? 'text-white' : 'text-neutral-500'}`}>
            Annual <span className="text-green-400 font-medium">save ~17%</span>
          </span>
        </div>
      </div>

      {/* Plan cards */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {PLANS.map(plan => {
          const c = COLOR[plan.color]
          const Icon = plan.icon
          return (
            <div
              key={plan.id}
              className={`relative rounded-2xl border ${c.border} bg-[var(--bg-surface)] p-6 flex flex-col gap-5 ${c.ring}`}
            >
              {plan.highlight && (
                <div className="absolute -top-3 left-0 right-0 flex justify-center">
                  <span className="px-3 py-1 text-[10px] font-bold text-purple-300 bg-purple-900/80 border border-purple-500/40 rounded-full uppercase tracking-widest">
                    Most popular
                  </span>
                </div>
              )}

              {/* Plan header */}
              <div className="space-y-2">
                <div className={`w-9 h-9 rounded-xl flex items-center justify-center ${c.icon}`}>
                  <Icon size={18} aria-hidden="true" />
                </div>
                <div>
                  <h2 className="text-base font-bold text-white">{plan.name}</h2>
                  <p className="text-[11px] text-neutral-500 leading-relaxed mt-0.5">{plan.desc}</p>
                </div>
                <div className="pt-1">
                  {plan.price ? (
                    <div className="flex items-end gap-1">
                      <span className="text-3xl font-bold text-white">
                        ${(annual ? plan.price.annual : plan.price.monthly).toLocaleString()}
                      </span>
                      <span className="text-xs text-neutral-500 mb-1">/mo{annual ? ', billed annually' : ''}</span>
                    </div>
                  ) : (
                    <div className="text-2xl font-bold text-white">Custom</div>
                  )}
                </div>
              </div>

              {/* CTA */}
              <button
                className={`w-full flex items-center justify-center gap-2 px-4 py-2.5 text-sm font-semibold rounded-xl transition-colors ${c.btn}`}
              >
                {plan.price ? 'Start free trial' : 'Contact sales'}
                <ArrowRight size={14} aria-hidden="true" />
              </button>

              {/* Limits */}
              <div className="space-y-2 border-t border-[var(--border-subtle)] pt-4">
                {LIMIT_LABELS.map(([key, label]) => {
                  const val = plan.limits[key]
                  return (
                    <div key={key} className="flex items-center justify-between gap-2">
                      <span className="text-[11px] text-neutral-500">{label}</span>
                      {typeof val === 'boolean' ? (
                        <span className={val ? 'text-green-400' : 'text-neutral-700'}>
                          {val ? <CheckCircle2 size={12} /> : '—'}
                        </span>
                      ) : (
                        <span className="text-[11px] text-neutral-300 font-medium text-right max-w-[60%]">{val}</span>
                      )}
                    </div>
                  )
                })}
              </div>

              {/* Features */}
              <div className="space-y-1.5">
                {plan.features.map(f => (
                  <div key={f} className="flex items-start gap-2">
                    <CheckCircle2 size={12} className="text-green-400 shrink-0 mt-0.5" aria-hidden="true" />
                    <span className="text-[11px] text-neutral-400 leading-relaxed">{f}</span>
                  </div>
                ))}
              </div>
            </div>
          )
        })}
      </div>

      {/* FAQ */}
      <div className="space-y-4">
        <h2 className="text-base font-bold text-white text-center">Frequently asked questions</h2>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {FAQS.map(({ q, a }) => (
            <div key={q} className="p-4 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] space-y-1.5">
              <h3 className="text-xs font-semibold text-white">{q}</h3>
              <p className="text-[11px] text-neutral-500 leading-relaxed">{a}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Enterprise CTA */}
      <div className="rounded-2xl border border-amber-500/20 bg-amber-500/[0.03] p-8 text-center space-y-3">
        <Building2 size={28} className="text-amber-400 mx-auto" aria-hidden="true" />
        <h2 className="text-lg font-bold text-white">Need a custom deployment?</h2>
        <p className="text-sm text-neutral-400 max-w-md mx-auto">
          Air-gapped VPC, WORM audit storage, custom SLAs, and dedicated infrastructure for regulated industries.
        </p>
        <button className="inline-flex items-center gap-2 px-5 py-2.5 text-sm font-semibold text-amber-900 bg-amber-400 hover:bg-amber-300 rounded-xl transition-colors">
          Talk to enterprise sales <ArrowRight size={14} aria-hidden="true" />
        </button>
      </div>
    </div>
  )
}
