import React from 'react'
import { Link } from 'react-router-dom'
import {
  Shield, ShieldCheck, Eye, Activity, MessagesSquare, FileBadge2,
  CheckCircle2, Lock, ArrowRight, Bot, Crosshair, Workflow,
} from 'lucide-react'

// Sprint 11 — Marketing landing.
//
// Anyone hitting / today gets redirected to /login. That's a missed
// conversion every single visit — the value prop is invisible. This
// page is the founder's positioning verbatim:
//
//   "Aegis is an AI governance and runtime security platform.
//    It sits between AI agents and the systems they control,
//    enforcing policy, requiring approvals, tracking usage, and
//    generating cryptographically verifiable audit trails."
//
// The page is server-renderable static HTML in spirit (no API
// calls, no auth state) so a curious enterprise buyer can land
// without an account and still understand what Aegis does.

const VALUE_PROPS = [
  {
    icon: Workflow,
    title: 'Governance, not pattern matching',
    body: 'Capabilities, approval workflows, and per-employee budgets — not just a regex list. ALLOW / DENY / ESCALATE / REQUIRE_APPROVAL_FROM(role) on every agent action.',
  },
  {
    icon: Shield,
    title: 'Runtime security at the gateway',
    body: '17 prompt-injection patterns, escalation rules for money movement / prod destruction / mass-data ops, and 5 compliance packs (SOC2 / PCI / HIPAA / Finance / DevOps).',
  },
  {
    icon: FileBadge2,
    title: 'Cryptographically verifiable audit',
    body: 'Every decision is rowed into an append-only log; daily Merkle roots are signed ed25519 and mirrored to a public S3 bucket. Any auditor can verify your evidence without trusting Aegis.',
  },
]

const MANDATE_QUESTIONS = [
  { q: 'Who uses AI in our company?',                a: 'Team page lists every employee with a virtual key, the model they used, and per-team spend.' },
  { q: 'How much is it costing us?',                a: 'Daily / monthly USD budgets per employee; aggregate spend per department on one screen.' },
  { q: 'What risky behavior was stopped?',          a: 'Dashboard tile: harmful actions blocked (30d). Each one is a Merkle-signed audit row.' },
  { q: 'Can we prove compliance?',                  a: 'Policy Pack enforcement page maps every escalation to the SOC2 / PCI / HIPAA control it covers.' },
]

// kept — shown as code example in UI (literal must match the customer's
// documented prod gateway URL so copy-paste works without edits).
const CODE_SNIPPET = `# Anthropic SDK — drop-in. The only change is base_url.
import anthropic
client = anthropic.Anthropic(
    api_key="acp_emp_…",                          # employee virtual key
    base_url="https://ha.aegisagent.in/v1",       # Aegis proxy
)
client.messages.create(
    model="claude-haiku-4-5",
    max_tokens=200,
    messages=[{"role": "user", "content": "…"}],
)`

const TRUST_STRIP = [
  {
    icon: Lock,
    label: 'Your LLM key stays on your machine',
    body: 'Aegis never asks for your Anthropic / OpenAI key. The only credential it issues is acp_…',
  },
  {
    icon: Eye,
    label: '14-day shadow mode by default',
    body: 'Every decision is logged. Nothing is blocked until you exit shadow mode.',
  },
  {
    icon: ShieldCheck,
    label: 'Cryptographic transparency log',
    body: 'Even if Aegis is compromised after a nightly bundle, any rewrite of history is publicly detectable.',
  },
]


function Hero() {
  return (
    <section className="px-6 py-20 lg:py-28 max-w-6xl mx-auto text-center">
      <div className="inline-flex items-center gap-2 text-[10px] uppercase tracking-widest text-neutral-500 mb-6">
        <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" aria-hidden="true" />
        Live · governs the API key your company gives Claude or GPT
      </div>
      <h1 className="text-4xl lg:text-5xl font-bold tracking-tight text-white leading-tight">
        AI governance &amp; runtime security platform
      </h1>
      <p className="text-sm lg:text-base text-neutral-300 leading-relaxed mt-5 max-w-2xl mx-auto">
        Aegis sits between AI agents and the systems they control —
        enforcing policy, requiring approvals, tracking usage, and
        generating cryptographically verifiable audit trails.
      </p>
      <div className="flex items-center justify-center gap-3 mt-8 flex-wrap">
        <Link
          to="/signup"
          className="inline-flex items-center gap-2 px-4 py-2.5 rounded-lg bg-white text-black text-sm font-semibold hover:bg-neutral-100 transition-colors"
        >
          Start free — 14-day shadow mode <ArrowRight size={14} />
        </Link>
        <Link
          to="/login"
          className="inline-flex items-center gap-2 px-4 py-2.5 rounded-lg border border-white/[0.12] text-sm font-medium text-neutral-200 hover:border-white/30 hover:text-white transition-colors"
        >
          Sign in
        </Link>
      </div>
      <div className="text-[11px] text-neutral-600 mt-4">
        No credit card. Your Anthropic / OpenAI key never reaches us.
      </div>
    </section>
  )
}


function ValueProps() {
  return (
    <section className="px-6 py-12 max-w-6xl mx-auto">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {VALUE_PROPS.map((v) => {
          const Icon = v.icon
          return (
            <div key={v.title} className="rounded-xl border border-white/[0.07] bg-[#0a0a0a] p-5 space-y-3">
              <div className="w-9 h-9 rounded-md bg-white text-black flex items-center justify-center">
                <Icon size={16} aria-hidden="true" />
              </div>
              <h3 className="text-sm font-bold text-white">{v.title}</h3>
              <p className="text-xs text-neutral-400 leading-relaxed">{v.body}</p>
            </div>
          )
        })}
      </div>
    </section>
  )
}


function MandateQuestions() {
  return (
    <section className="px-6 py-12 max-w-6xl mx-auto">
      <div className="text-[10px] uppercase tracking-widest text-neutral-500 mb-3 flex items-center gap-2">
        <Activity size={11} />
        <span>Four questions a CIO opens Aegis with</span>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {MANDATE_QUESTIONS.map((m) => (
          <div key={m.q} className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-4 space-y-1">
            <div className="text-sm font-semibold text-white">{m.q}</div>
            <p className="text-xs text-neutral-400 leading-relaxed">{m.a}</p>
          </div>
        ))}
      </div>
    </section>
  )
}


function CodeBlock() {
  return (
    <section className="px-6 py-12 max-w-6xl mx-auto">
      <div className="rounded-2xl border border-white/[0.07] bg-[#050505] p-6 lg:p-8 space-y-4">
        <div className="text-[10px] uppercase tracking-widest text-neutral-500 flex items-center gap-2">
          <Bot size={11} />
          <span>Drop-in. Two lines of Python change.</span>
        </div>
        <pre className="text-xs lg:text-[13px] font-mono text-neutral-200 leading-relaxed whitespace-pre overflow-x-auto">
{CODE_SNIPPET}
        </pre>
        <div className="text-[11px] text-neutral-500 leading-snug">
          From the SDK's point of view nothing changed. From Aegis's point of view:
          per-employee virtual key, daily + monthly USD budget enforced before the
          upstream call, prompt scanned against 17 injection patterns + your
          compliance pack rules, decision rowed into the Merkle-chained audit log.
        </div>
      </div>
    </section>
  )
}


function TrustStrip() {
  return (
    <section className="px-6 py-12 max-w-6xl mx-auto">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        {TRUST_STRIP.map((t) => {
          const Icon = t.icon
          return (
            <div key={t.label} className="flex gap-3 items-start rounded-xl border border-white/[0.07] bg-[#0a0a0a] p-4">
              <div className="w-7 h-7 rounded-md bg-white/[0.05] flex items-center justify-center text-neutral-200 shrink-0 mt-0.5">
                <Icon size={13} aria-hidden="true" />
              </div>
              <div className="min-w-0">
                <div className="text-xs font-semibold text-white">{t.label}</div>
                <p className="text-[11px] text-neutral-400 leading-snug mt-0.5">{t.body}</p>
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}


function Footer() {
  return (
    <footer className="px-6 py-10 mt-12 border-t border-white/[0.06] max-w-6xl mx-auto">
      <div className="flex items-center justify-between flex-wrap gap-3 text-[11px] text-neutral-500">
        <div className="flex items-center gap-2">
          <Shield size={12} />
          <span className="text-neutral-300 font-semibold">Aegis</span>
          <span>·</span>
          <span>AI governance &amp; runtime security platform</span>
        </div>
        <div className="flex items-center gap-4">
          <a href="/status" className="hover:text-white">Status</a>
          <Link to="/login" className="hover:text-white">Sign in</Link>
          <Link to="/signup" className="hover:text-white">Start free</Link>
        </div>
      </div>
    </footer>
  )
}


export default function Landing() {
  return (
    <div className="min-h-screen bg-[#040404] text-neutral-100">
      {/* Top nav — minimal, just brand + sign in */}
      <header className="border-b border-white/[0.06] px-6 py-4">
        <div className="max-w-6xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-md bg-white text-black flex items-center justify-center">
              <Shield size={14} aria-hidden="true" />
            </div>
            <span className="text-sm font-bold text-white tracking-tight">Aegis</span>
          </div>
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

      <Hero />
      <ValueProps />
      <MandateQuestions />
      <CodeBlock />
      <TrustStrip />
      <Footer />
    </div>
  )
}
