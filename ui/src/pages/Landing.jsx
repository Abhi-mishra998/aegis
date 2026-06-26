import React, { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  Shield, ShieldCheck, Eye, Activity, MessagesSquare, FileBadge2,
  CheckCircle2, Lock, ArrowRight, Bot, Crosshair, Workflow,
  Sparkles, Loader2, AlertCircle,
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
    base_url="https://aegisagent.in/v1",       # Aegis proxy
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
  const navigate = useNavigate()
  // Sprint U10 — "Spawn demo workspace" CTA. POSTs to /demo/spawn-workspace
  // on the gateway (cookieless guest endpoint). On success the prospect
  // lands inside a 14-day shadow-mode dashboard pre-seeded with a sample
  // agent + Aegis API key, so they can poke around before signing up.
  const [spawning, setSpawning] = useState(false)
  const [spawnError, setSpawnError] = useState('')

  const onSpawnDemo = async () => {
    if (spawning) return
    setSpawning(true)
    setSpawnError('')
    // After ANY non-success outcome we keep the button disabled for a short
    // cooldown so a frustrated user can't burn a 50-call rate-limit bucket
    // in five seconds. On success we navigate away and the disabled state
    // is moot.
    const cooldown = (ms) => new Promise((r) => setTimeout(r, ms))
    try {
      // Match the rest of the SPA (services/api.js, ClerkAuthBridge, useSSE):
      // empty base in prod → nginx proxies /demo/* to the gateway. The old
      // `/api` default fell through to the SPA `try_files` block → 405.
      const apiBase = import.meta.env?.VITE_GATEWAY_URL || ''
      const res = await fetch(`${apiBase}/demo/spawn-workspace`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Request-ID':
            typeof crypto !== 'undefined' && crypto.randomUUID
              ? crypto.randomUUID()
              : `lnd_${Date.now()}`,
        },
        body: JSON.stringify({ source: 'landing_hero_cta' }),
      })
      if (!res.ok) {
        // 404 = backend hasn't shipped the endpoint yet — fall through to
        // the signup flow so the click still converts.
        if (res.status === 404) {
          navigate('/signup')
          return
        }
        // 429 = per-IP cap (50 spawns / 10 min). Show the real reason
        // instead of the generic "try Start free" so the user understands
        // why retrying immediately won't help.
        if (res.status === 429) {
          setSpawnError(
            "Too many demo workspaces from your network in the last 10 minutes. " +
            "Wait a few minutes or use Start free below — it's the same product.",
          )
          await cooldown(15000)
          return
        }
        // 403 = WAF / Turnstile / XFF refusal — almost always recoverable
        // by going through the signup flow, which the user-friendly path is.
        if (res.status === 403) {
          setSpawnError(
            'The demo spawn was blocked by our edge security. Use Start free below to continue.',
          )
          await cooldown(8000)
          return
        }
        throw new Error(`HTTP ${res.status}`)
      }
      const data = await res.json().catch(() => ({}))
      // Backend wraps the payload in {success, data:{...}} — unwrap if present.
      const payload = data?.data ?? data
      const target =
        payload?.redirect_url || payload?.dashboard_url || '/dashboard'
      // Always use a full-page navigation here, never React Router's client-side
      // navigate(). The demo redirect carries ?demo_token=<JWT> and the only
      // code that consumes it BEFORE ProtectedRoute's synchronous
      // redirect-to-login is the IIFE at main.jsx:17 — which only runs on
      // module load. A client-side navigate() reuses the loaded module, the
      // IIFE never re-fires, sessionStorage stays empty, ProtectedRoute
      // bounces the user to /login. window.location.assign forces the full
      // reload that lets the IIFE install the cookie + session metadata
      // before React first renders.
      window.location.assign(target)
    } catch (err) {
      setSpawnError(
        'Could not reach the demo service right now. Use Start free below to continue.',
      )
      await cooldown(8000)
    } finally {
      setSpawning(false)
    }
  }

  return (
    <section className="px-4 sm:px-6 py-16 sm:py-20 lg:py-28 max-w-6xl mx-auto text-center">
      <div className="inline-flex items-center gap-2 text-[10px] uppercase tracking-widest text-neutral-500 mb-6">
        <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" aria-hidden="true" />
        Live · governs the API key your company gives Claude or GPT
      </div>
      <h1 className="text-3xl sm:text-4xl lg:text-5xl font-bold tracking-tight text-white leading-tight">
        AI governance &amp; runtime security platform
      </h1>
      <p className="text-sm lg:text-base text-neutral-300 leading-relaxed mt-5 max-w-2xl mx-auto px-2">
        Aegis sits between AI agents and the systems they control —
        enforcing policy, requiring approvals, tracking usage, and
        generating cryptographically verifiable audit trails.
      </p>
      <div className="flex items-center justify-center gap-3 mt-8 flex-wrap">
        <button
          type="button"
          onClick={onSpawnDemo}
          disabled={spawning}
          aria-busy={spawning}
          className="inline-flex items-center gap-2 px-4 py-2.5 rounded-lg bg-white text-black text-sm font-semibold hover:bg-neutral-100 disabled:opacity-70 disabled:cursor-wait transition-colors min-w-[220px] justify-center"
        >
          {spawning ? (
            <>
              <Loader2 size={14} className="animate-spin" aria-hidden="true" />
              Spawning demo workspace…
            </>
          ) : (
            <>
              <Sparkles size={14} aria-hidden="true" />
              Spawn demo workspace
            </>
          )}
        </button>
        <Link
          to="/signup"
          className="inline-flex items-center gap-2 px-4 py-2.5 rounded-lg border border-white/[0.12] text-sm font-medium text-neutral-200 hover:border-white/30 hover:text-white transition-colors"
        >
          Start free — 14-day shadow mode <ArrowRight size={14} aria-hidden="true" />
        </Link>
        <Link
          to="/login"
          className="inline-flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium text-neutral-400 hover:text-white transition-colors"
        >
          Sign in
        </Link>
      </div>
      {spawnError && (
        <div
          role="alert"
          className="mt-4 mx-auto max-w-md inline-flex items-start gap-2 text-[11px] text-red-300 bg-red-500/[0.06] border border-red-500/20 rounded-lg px-3 py-2"
        >
          <AlertCircle size={12} className="mt-0.5 shrink-0" aria-hidden="true" />
          <span className="text-left">{spawnError}</span>
        </div>
      )}
      <div className="text-[11px] text-neutral-600 mt-4">
        No credit card. Your Anthropic / OpenAI key never reaches us.
      </div>
    </section>
  )
}


function ValueProps() {
  return (
    <section className="px-4 sm:px-6 py-12 max-w-6xl mx-auto">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
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
    <section className="px-4 sm:px-6 py-12 max-w-6xl mx-auto">
      <div className="text-[10px] uppercase tracking-widest text-neutral-500 mb-3 flex items-center gap-2">
        <Activity size={11} aria-hidden="true" />
        <span>Four questions a CIO opens Aegis with</span>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
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
    <section className="px-4 sm:px-6 py-12 max-w-6xl mx-auto">
      <div className="rounded-2xl border border-white/[0.07] bg-[#050505] p-5 sm:p-6 lg:p-8 space-y-4">
        <div className="text-[10px] uppercase tracking-widest text-neutral-500 flex items-center gap-2">
          <Bot size={11} aria-hidden="true" />
          <span>Drop-in. Two lines of Python change.</span>
        </div>
        <pre className="text-[11px] sm:text-xs lg:text-[13px] font-mono text-neutral-200 leading-relaxed whitespace-pre overflow-x-auto">
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
    <section className="px-4 sm:px-6 py-12 max-w-6xl mx-auto">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
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
    <footer className="px-4 sm:px-6 py-10 mt-12 border-t border-white/[0.06] max-w-6xl mx-auto">
      <div className="flex items-center justify-between flex-wrap gap-3 text-[11px] text-neutral-500">
        <div className="flex items-center gap-2">
          <Shield size={12} aria-hidden="true" />
          <span className="text-neutral-300 font-semibold">Aegis</span>
          <span aria-hidden="true">·</span>
          <span>AI governance &amp; runtime security platform</span>
        </div>
        <div className="flex items-center gap-4">
          <Link to="/pricing" className="hover:text-white">Pricing</Link>
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
      <header className="border-b border-white/[0.06] px-4 sm:px-6 py-4">
        <div className="max-w-6xl mx-auto flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-md bg-white text-black flex items-center justify-center">
              <Shield size={14} aria-hidden="true" />
            </div>
            <span className="text-sm font-bold text-white tracking-tight">Aegis</span>
          </div>
          <div className="flex items-center gap-2">
            <Link to="/pricing" className="text-xs text-neutral-300 hover:text-white px-3 py-1.5 rounded-md transition-colors hidden sm:inline-flex">
              Pricing
            </Link>
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
