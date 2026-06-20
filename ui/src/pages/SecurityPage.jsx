import React from 'react'
import { Link } from 'react-router-dom'
import {
  Shield, Mail, FileText, Clock, ArrowRight, ExternalLink,
  CheckCircle2, AlertTriangle, BookOpen, Award,
} from 'lucide-react'

/**
 * /security — Public responsible-disclosure landing page.
 *
 * Sprint EI-22 (2026-06-21). Pairs with the existing
 * /.well-known/security.txt (RFC 9116). This page is the human-readable
 * surface — security.txt is the machine-readable directory entry.
 *
 * Also mirrored as a static HTML at s3://aegis-public-roots/security/
 * index.html (same outage-resistance pattern as /status from EI-14) so
 * a researcher can reach the policy even if the Aegis ALB is down.
 *
 * Be honest about what we DO and DO NOT offer:
 *  - We DO offer: 48h ack, 5d triage, 90d fix for HIGH+CRITICAL,
 *    public credit (or anonymity, your call), safe-harbor for good-
 *    faith research, response from a real engineer (not a SaaS triage
 *    queue).
 *  - We DO NOT offer (today): monetary bounty. Aegis is pre-revenue
 *    and our bounty budget is $0. We commit to introducing a paid
 *    tier when ARR clears $1M and to retroactively pay the highest-
 *    severity reports up to that date.
 */

const RESPONSE_SLOS = [
  { label: 'Acknowledge receipt',          window: '48 hours',         icon: Mail },
  { label: 'Triage decision',              window: '5 business days',  icon: FileText },
  { label: 'Fix for HIGH / CRITICAL',      window: '90 days (faster on agreement)', icon: Clock },
  { label: 'Public credit (if accepted)',  window: 'Next security advisory', icon: Award },
]

const IN_SCOPE = [
  'Aegis API surface at aegisagent.in / eu.aegisagent.in',
  'staging.aegisagent.in (synthetic data only)',
  'Any Aegis SDK published to PyPI (aegis-aevf, aegis-anthropic, aegis-openai, aegis-bedrock, aegis-langchain)',
  'The Aegis container images we publish (when listed in our Customer Security Package SBOM)',
  'Vulnerabilities in our published documentation that demonstrate a real attack',
]

const OUT_OF_SCOPE = [
  'Other tenants\' data — even if you legitimately find a cross-tenant leak via the demo workspace, do NOT pivot to attempt to read other tenants. Report and we will reproduce.',
  'Social engineering against our employees or our suppliers',
  'Physical attacks against our infrastructure',
  'Denial-of-service attacks (please don\'t — we have a small WAF budget)',
  'Findings in third-party SaaS we use (Clerk, Stripe, AWS) — report directly to that vendor',
  'Best-practice warnings without a demonstrable security impact (e.g. "missing Content-Security-Policy" without a real XSS chain)',
]

const REWARDS = [
  {
    severity: 'CRITICAL',
    examples: 'RCE, auth bypass, cross-tenant data leak, audit-chain tamper',
    today: 'Public credit + retroactive payment when we introduce a paid tier ($5k+ committed)',
    tomorrow: 'Up to $20,000 once ARR > $1M',
  },
  {
    severity: 'HIGH',
    examples: 'Privilege escalation, RBAC bypass, secret leak in logs',
    today: 'Public credit + retroactive payment ($1k+ committed)',
    tomorrow: 'Up to $5,000',
  },
  {
    severity: 'MEDIUM',
    examples: 'Cache poisoning, SSRF without exfil, info disclosure',
    today: 'Public credit',
    tomorrow: 'Up to $500',
  },
  {
    severity: 'LOW',
    examples: 'CSRF on low-impact endpoint, missing security header on a non-sensitive page',
    today: 'Public credit (at our discretion)',
    tomorrow: 'Public credit',
  },
]


function Header () {
  return (
    <section className="px-6 py-16 lg:py-20 max-w-5xl mx-auto text-center">
      <div className="inline-flex items-center gap-2 text-[10px] uppercase tracking-widest text-neutral-500 mb-4">
        <Shield size={11} aria-hidden="true" />
        Responsible disclosure
      </div>
      <h1 className="text-3xl lg:text-4xl font-bold tracking-tight text-white">
        We treat the security of a security product as the first concern.
      </h1>
      <p className="text-sm lg:text-base text-neutral-400 leading-relaxed mt-4 max-w-2xl mx-auto">
        If you find something, we want to hear from you — quickly, privately,
        and with credit. No legal threats, no NDAs, no triage queue. A real
        engineer responds in under 48 hours.
      </p>
      <div className="flex items-center justify-center gap-3 mt-8 flex-wrap">
        <a
          href="mailto:security@aegisagent.in?subject=Security%20report"
          className="inline-flex items-center gap-2 px-4 py-2.5 rounded-lg bg-white text-black text-sm font-semibold hover:bg-neutral-100 transition-colors"
        >
          <Mail size={14} /> Email security@aegisagent.in
        </a>
        <a
          href="https://github.com/Abhi-mishra998/aegis/security/advisories/new"
          target="_blank" rel="noopener noreferrer"
          className="inline-flex items-center gap-2 px-4 py-2.5 rounded-lg border border-white/[0.20] bg-white/[0.04] text-sm font-medium text-white hover:bg-white/[0.08] hover:border-white/30 transition-colors"
        >
          <BookOpen size={14} /> Open a GitHub Security Advisory <ExternalLink size={11} />
        </a>
      </div>
    </section>
  )
}


function SLOs () {
  return (
    <section className="px-6 max-w-5xl mx-auto">
      <h2 className="text-xs uppercase tracking-widest text-neutral-500 mb-3">Our response SLOs</h2>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {RESPONSE_SLOS.map((s) => (
          <div key={s.label} className="p-4 rounded-xl border border-white/[0.08] bg-white/[0.02]">
            <div className="flex items-start gap-3">
              <s.icon size={16} className="text-white shrink-0 mt-0.5" aria-hidden="true" />
              <div>
                <div className="text-sm font-semibold text-white">{s.label}</div>
                <div className="text-xs text-neutral-400 mt-1">within {s.window}</div>
              </div>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}


function ScopeList ({ title, items, icon: Icon, accent }) {
  return (
    <div className="p-5 rounded-xl border border-white/[0.08] bg-white/[0.02]">
      <h3 className={`text-sm font-semibold flex items-center gap-2 mb-3 ${accent}`}>
        <Icon size={14} aria-hidden="true" />
        {title}
      </h3>
      <ul className="space-y-2 text-xs text-neutral-300 leading-relaxed">
        {items.map((i, idx) => (
          <li key={idx} className="flex items-start gap-2">
            <span className="text-neutral-600 select-none mt-0.5">•</span>
            <span>{i}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}


function Scope () {
  return (
    <section className="px-6 py-12 max-w-5xl mx-auto">
      <h2 className="text-xs uppercase tracking-widest text-neutral-500 mb-3">Scope</h2>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <ScopeList title="In scope"     icon={CheckCircle2}  accent="text-green-400" items={IN_SCOPE} />
        <ScopeList title="Out of scope" icon={AlertTriangle} accent="text-amber-400" items={OUT_OF_SCOPE} />
      </div>
    </section>
  )
}


function SafeHarbor () {
  return (
    <section className="px-6 py-12 max-w-5xl mx-auto">
      <div className="p-5 rounded-xl border border-blue-500/20 bg-blue-500/[0.04]">
        <h2 className="text-xs uppercase tracking-widest text-blue-300/80 mb-2">Safe harbor</h2>
        <p className="text-sm text-neutral-200 leading-relaxed">
          We will not pursue civil action or initiate law-enforcement action
          against you for good-faith research that complies with this policy.
          This includes accidental DMCA / CFAA / equivalent claims — we
          waive them. Make a good-faith effort to avoid data exfiltration,
          service degradation, and access to other tenants' data, and
          notify us immediately if you encounter any of those during
          research.
        </p>
        <p className="text-sm text-neutral-200 leading-relaxed mt-3">
          If a third party (a SaaS vendor we use, a sub-processor, a customer
          who claims you breached their CFAA) brings action against you for
          research conducted under this policy, contact us — we will
          intervene in good faith to clarify that the research was
          authorised by Aegis.
        </p>
      </div>
    </section>
  )
}


function Rewards () {
  return (
    <section className="px-6 py-12 max-w-5xl mx-auto">
      <h2 className="text-xs uppercase tracking-widest text-neutral-500 mb-3">Rewards — honest version</h2>
      <p className="text-xs text-neutral-500 leading-relaxed mb-4 max-w-2xl">
        Aegis is pre-revenue. Today we offer public credit + a written
        commitment to retroactively pay your finding once we cross
        $1M ARR. The "Tomorrow" column is the bounty we commit to
        introducing at that milestone. We will email you when the paid
        tier opens.
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-[10px] uppercase tracking-wide text-neutral-500 border-b border-white/[0.08]">
            <tr>
              <th className="text-left py-2 pr-4">Severity</th>
              <th className="text-left py-2 pr-4">Examples</th>
              <th className="text-left py-2 pr-4">Today (pre-revenue)</th>
              <th className="text-left py-2">Tomorrow (post-$1M ARR)</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/[0.05]">
            {REWARDS.map((r) => (
              <tr key={r.severity} className="align-top">
                <td className="py-3 pr-4 font-mono text-[11px] text-white">{r.severity}</td>
                <td className="py-3 pr-4 text-neutral-300">{r.examples}</td>
                <td className="py-3 pr-4 text-neutral-300">{r.today}</td>
                <td className="py-3 text-neutral-300">{r.tomorrow}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}


function Credits () {
  return (
    <section className="px-6 py-12 max-w-5xl mx-auto">
      <h2 className="text-xs uppercase tracking-widest text-neutral-500 mb-3">Hall of credit</h2>
      <div className="p-5 rounded-xl border border-white/[0.08] bg-white/[0.02]">
        <p className="text-sm text-neutral-300 leading-relaxed">
          Be the first researcher in the ledger. Email{' '}
          <a className="text-white underline hover:no-underline"
             href="mailto:security@aegisagent.in?subject=Security%20report">
            security@aegisagent.in
          </a>{' '}
          and your handle (or real name — your choice) lands here on the
          next security advisory.
        </p>
      </div>
    </section>
  )
}


function Foot () {
  return (
    <section className="px-6 py-16 max-w-5xl mx-auto text-center text-xs text-neutral-500">
      <p>
        Machine-readable directory entry:{' '}
        <a className="text-white underline hover:no-underline"
           href="/.well-known/security.txt">/.well-known/security.txt</a> (RFC 9116).
      </p>
      <p className="mt-2">
        This page is also mirrored at{' '}
        <a className="text-white underline hover:no-underline"
           href="https://aegis-public-roots-628478946931.s3.amazonaws.com/security/index.html"
           target="_blank" rel="noopener noreferrer">
          s3://aegis-public-roots/security/index.html
        </a>{' '}
        — reachable even during an Aegis outage.
      </p>
      <div className="mt-6">
        <Link to="/trust" className="inline-flex items-center gap-1 text-neutral-500 hover:text-white">
          <ArrowRight size={11} aria-hidden="true" className="rotate-180" />
          Back to Trust Center
        </Link>
      </div>
    </section>
  )
}


export default function SecurityPage () {
  return (
    <div className="min-h-screen bg-black text-neutral-200">
      <Header />
      <SLOs />
      <Scope />
      <SafeHarbor />
      <Rewards />
      <Credits />
      <Foot />
    </div>
  )
}
