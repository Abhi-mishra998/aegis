import React from 'react'
import {
  Github, Star, GitFork, Scale, ShieldCheck, Zap, Database, Globe,
  ArrowRight, BookOpen, Terminal, Users, Network, Lock, Activity,
  CheckCircle2, Code2, GitBranch, Package, Award, ExternalLink,
} from 'lucide-react'

// Senior-engineer open-source landing page. Modeled on Kubernetes,
// Prometheus, OpenTelemetry, Grafana, and Postgres project pages:
// short, technical, no marketing fluff. Everything below is verifiable
// against the live deployment at https://aegisagent.in.

const GH_REPO = 'https://github.com/Abhi-mishra998/aegis'

const PRIMITIVES = [
  {
    icon: ShieldCheck,
    title: 'Tamper-evident audit',
    body:  'Every decision is a SHA-256 row chained to the previous one and signed with ed25519. Verifiable offline by any auditor with the public key.',
  },
  {
    icon: Lock,
    title: 'Hard-deny pipeline',
    body:  'PII, RCE, SQL injection, and destructive Kubernetes ops are detected at the gateway and rejected with HTTP 403 in ~50 ms — before the policy engine runs.',
  },
  {
    icon: Activity,
    title: '5-signal risk scoring',
    body:  'Inference, behavior, anomaly, cost, and cross-agent classifiers combine to one composite. Weights are tenant-tunable through a Redis key.',
  },
  {
    icon: Network,
    title: 'OPA policy engine',
    body:  'Rego policies live alongside the ML signals. Both must agree for an allow. Fail-closed on policy outage so a degraded engine never widens trust.',
  },
  {
    icon: Database,
    title: 'Transparency log',
    body:  'Daily Merkle root commits over signed receipts. Even total root-key compromise is publicly detectable to anyone who archived an earlier root.',
  },
  {
    icon: Zap,
    title: 'Sub-second SSE',
    body:  'Server-Sent Events from Redis Pub/Sub fan out per-tenant + per-agent channels. tool_executed, policy_decision, kill_switch in real time.',
  },
]

const ARCHITECTURE_LAYERS = [
  { tag: 'L7',  name: 'API Gateway',       desc: 'FastAPI · JWT + bcrypt · sliding-window rate limit · idempotency keys' },
  { tag: 'L6',  name: 'Inference Proxy',   desc: 'PII / RCE / SQLi / k8s-destructive detection · ~50 ms hard-deny' },
  { tag: 'L5',  name: 'Policy Engine',     desc: 'OPA · Rego · bundle server · 60 s Redis cache' },
  { tag: 'L4',  name: 'Decision Engine',   desc: '5-signal composite scoring · per-tenant signal weights' },
  { tag: 'L3',  name: 'Behavior Service',  desc: 'Sequence + velocity + cost + cross-agent anomaly' },
  { tag: 'L2',  name: 'Audit Chain',       desc: 'PostgreSQL · ed25519-signed rows · sharded prev_hash chain' },
  { tag: 'L1',  name: 'Transparency Log',  desc: 'Daily Merkle root · signed · chained across days' },
  { tag: 'L0',  name: 'Observability',     desc: 'Prometheus · Grafana · Jaeger · Alertmanager' },
]

const STACK = [
  { kind: 'Runtime',       items: ['Python 3.11', 'FastAPI', 'asyncio', 'uvicorn'] },
  { kind: 'Storage',       items: ['PostgreSQL 15', 'Redis 7', 'pgbouncer', 'S3'] },
  { kind: 'Policy',        items: ['Open Policy Agent', 'Rego', 'OPAL bundle server'] },
  { kind: 'Crypto',        items: ['ed25519 receipts', 'SHA-256 chain', 'Merkle root'] },
  { kind: 'Observability', items: ['Prometheus', 'Grafana', 'Jaeger / OTel', 'Alertmanager'] },
  { kind: 'Deploy',        items: ['Docker Compose', 'AWS EC2 + ALB', 'RDS', 'ElastiCache', 'ACM'] },
]

const SAMPLE_SNIPPET = `# 1. Login (returns JWT)
TOKEN=$(curl -s -X POST https://aegisagent.in/auth/token \\
  -H "Content-Type: application/json" \\
  -H "X-Tenant-ID: 00000000-0000-0000-0000-000000000001" \\
  -d '{"email":"demo@aegisagent.in","password":"demo1234"}' \\
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['access_token'])")

# 2. Safe call → ALLOW
curl -s -X POST https://aegisagent.in/execute \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "X-Tenant-ID: 00000000-0000-0000-0000-000000000001" \\
  -H "Content-Type: application/json" \\
  -d '{"agent_id":"a245cc68-19aa-48a7-8862-f3d7f0332ff6","tool":"search_web","parameters":{"query":"AI governance"}}'

# 3. PII exfiltration attempt → HTTP 403
curl -s -X POST https://aegisagent.in/execute \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "X-Tenant-ID: 00000000-0000-0000-0000-000000000001" \\
  -H "Content-Type: application/json" \\
  -d '{"agent_id":"a245cc68-19aa-48a7-8862-f3d7f0332ff6","tool":"send_email","parameters":{"body":"SSN is 123-45-6789"}}'`

const NUMBERS = [
  { value: '12',    unit: 'services',   sub: 'all healthy in prod' },
  { value: '24',    unit: 'containers', sub: 'per EC2, Compose-orchestrated' },
  { value: '11K+',  unit: 'decisions',  sub: 'logged & chain-verified' },
  { value: '21 ms', unit: 'p95',        sub: 'end-to-end gateway latency' },
  { value: '0',     unit: 'violations', sub: 'chain integrity' },
  { value: '~50 ms',unit: 'hard-deny',  sub: 'PII / RCE / SQLi / k8s prod' },
]

const FEATURES = [
  'Multi-tenant authentication (JWT + bcrypt, httpOnly cookie)',
  'OPA policy engine + Rego bundles',
  '5-classifier ML risk scoring',
  'PII / RCE / SQLi / k8s hard-deny pipeline',
  'ed25519-signed audit rows',
  'SHA-256 hash-chained audit table',
  'Daily Merkle transparency root',
  'Server-Sent Events per-tenant + per-agent',
  'Per-agent cost cap (Redis-backed)',
  'Sliding-window rate limit (10 s + 60 s + daily + monthly)',
  'Tenant kill-switch (instant tenant-wide isolation)',
  'Per-agent kill toggle',
  'Autonomy contracts (state machine + override timeline)',
  'Flight Recorder (step-by-step replay per request)',
  'Identity graph (agent → tool → outcome edges)',
  'Forensics (investigation, replay, blast-radius)',
  'Webhook + Slack + PagerDuty alerts',
  'SIEM forwarding (Splunk HEC + Datadog Logs)',
  'Scheduled compliance reports (EU AI Act / NIST / SOC 2)',
  'Receipt verification SDK (offline, ed25519)',
]

export default function Pricing() {
  return (
    // Constrain the entire page to a comfortable reading width and centre it.
    // MainLayout already provides outer max-w-[1600px] + horizontal padding;
    // here we further narrow the open-source landing to a documentation-grade
    // measure so headings don't sprawl to 1500px on wide displays.
    <div className="mx-auto w-full max-w-5xl space-y-16 pb-16">

      {/* ── Hero ───────────────────────────────────────────────────────── */}
      <section className="space-y-6">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] font-mono uppercase tracking-widest text-neutral-500">
          <span className="inline-flex items-center px-2 py-0.5 rounded-full border border-green-500/30 text-green-400 bg-green-500/[0.06]">
            v4.4.0
          </span>
          <span aria-hidden="true">·</span>
          <span>Apache 2.0</span>
          <span aria-hidden="true">·</span>
          <span>Self-hostable</span>
          <span aria-hidden="true">·</span>
          <a href={GH_REPO} target="_blank" rel="noopener noreferrer" className="hover:text-white transition-colors">
            Abhi-mishra998 / aegis
          </a>
        </div>

        <h1 className="text-4xl sm:text-5xl font-black text-white tracking-tight leading-[1.05] max-w-3xl">
          Open-source runtime governance for AI agents.
        </h1>
        <p className="text-base text-neutral-400 leading-relaxed max-w-2xl">
          AgentControl sits between your application and any AI agent. Every tool call passes through
          the gateway, which decides in milliseconds whether to allow or block it, writes a
          cryptographically chained audit row, and emits a real-time event your dashboards consume —
          without you touching agent code.
        </p>

        <div className="flex flex-wrap items-center gap-3 pt-2">
          <a
            href={GH_REPO}
            target="_blank" rel="noopener noreferrer"
            className="inline-flex items-center gap-2 h-10 px-4 rounded-lg bg-white text-black text-xs font-bold uppercase tracking-wider hover:bg-neutral-200 transition-colors"
          >
            <Github size={14} aria-hidden="true" />
            View on GitHub
            <ArrowRight size={12} aria-hidden="true" />
          </a>
          <a
            href="https://aegisagent.in"
            className="inline-flex items-center gap-2 h-10 px-4 rounded-lg border border-white/15 text-white text-xs font-bold uppercase tracking-wider hover:border-white/40 hover:bg-white/[0.04] transition-colors"
          >
            <Terminal size={13} aria-hidden="true" />
            Live demo
            <ExternalLink size={11} aria-hidden="true" />
          </a>
          <a
            href="/developer"
            className="inline-flex items-center gap-2 h-10 px-4 rounded-lg border border-white/10 text-neutral-300 text-xs font-bold uppercase tracking-wider hover:border-white/30 hover:text-white transition-colors"
          >
            <BookOpen size={13} aria-hidden="true" />
            Docs
          </a>
        </div>

        {/* Numbers band — borrowed from the K8s / Postgres pages: dense,
            tabular, immediately quantifies the project rather than the
            usual marketing prose. */}
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-3 pt-6">
          {NUMBERS.map((n) => (
            <div key={n.value} className="bg-white/[0.02] border border-white/5 rounded-xl p-4">
              <div className="text-2xl font-black text-white tabular-nums leading-none">{n.value}</div>
              <div className="text-[9px] font-mono uppercase tracking-wider text-neutral-500 mt-1.5">{n.unit}</div>
              <div className="text-[10px] text-neutral-600 mt-1 leading-tight">{n.sub}</div>
            </div>
          ))}
        </div>
      </section>

      {/* ── What it is ─────────────────────────────────────────────────── */}
      <section className="space-y-5">
        <header className="space-y-1">
          <p className="text-[10px] font-mono uppercase tracking-widest text-neutral-600">## what it is</p>
          <h2 className="text-2xl font-black text-white tracking-tight">Six primitives. One pipeline.</h2>
        </header>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {PRIMITIVES.map(({ icon: Icon, title, body }) => (
            <div
              key={title}
              className="bg-white/[0.02] border border-white/5 rounded-xl p-5 hover:border-white/15 hover:bg-white/[0.03] transition-colors flex flex-col"
            >
              <div className="w-9 h-9 rounded-lg bg-white/[0.05] border border-white/[0.08] flex items-center justify-center mb-3 shrink-0">
                <Icon size={16} className="text-neutral-300" aria-hidden="true" />
              </div>
              <h3 className="text-sm font-bold text-white mb-1.5">{title}</h3>
              <p className="text-xs text-neutral-400 leading-relaxed">{body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ── Try it in 30 seconds ───────────────────────────────────────── */}
      <section className="space-y-4">
        <header className="space-y-1">
          <p className="text-[10px] font-mono uppercase tracking-widest text-neutral-600">## try it</p>
          <h2 className="text-2xl font-black text-white tracking-tight">Three commands against the live demo.</h2>
          <p className="text-sm text-neutral-500 max-w-2xl pt-1">
            Copy → paste → run. No signup, no API keys, no install. Step 1 sets{' '}
            <code className="text-blue-300 font-mono text-[12px] px-1 py-0.5 rounded bg-blue-500/[0.06] border border-blue-500/15">$TOKEN</code>{' '}
            which Steps 2 and 3 reuse. The hostile request is blocked in ~50 ms with HTTP 403 and a chain-anchored audit row.
          </p>
        </header>
        <div className="rounded-xl border border-white/5 bg-black/40 overflow-hidden">
          <div className="flex items-center justify-between px-4 py-2 border-b border-white/5">
            <div className="flex items-center gap-2">
              <div className="flex gap-1.5" aria-hidden="true">
                <span className="w-2.5 h-2.5 rounded-full bg-red-500/40" />
                <span className="w-2.5 h-2.5 rounded-full bg-yellow-500/40" />
                <span className="w-2.5 h-2.5 rounded-full bg-green-500/40" />
              </div>
              <span className="text-[10px] font-mono text-neutral-500 ml-2">bash</span>
            </div>
            <button
              type="button"
              onClick={() => navigator.clipboard?.writeText(SAMPLE_SNIPPET)}
              className="text-[10px] font-mono text-neutral-500 hover:text-white transition-colors"
            >
              copy
            </button>
          </div>
          <pre className="px-4 py-4 text-[11px] sm:text-xs font-mono text-green-300 overflow-x-auto whitespace-pre leading-relaxed">
            {SAMPLE_SNIPPET}
          </pre>
        </div>
      </section>

      {/* ── Architecture ───────────────────────────────────────────────── */}
      <section className="space-y-5">
        <header className="space-y-1">
          <p className="text-[10px] font-mono uppercase tracking-widest text-neutral-600">## architecture</p>
          <h2 className="text-2xl font-black text-white tracking-tight">Eight layers. Each one fails closed.</h2>
        </header>

        <div className="grid grid-cols-1 lg:grid-cols-[1fr_18rem] gap-6 items-start">
          <ol className="space-y-1.5">
            {ARCHITECTURE_LAYERS.map((layer) => (
              <li
                key={layer.tag}
                className="flex items-start gap-4 p-3 rounded-lg border border-white/5 bg-white/[0.015] hover:border-white/15 transition-colors"
              >
                <span className="text-[10px] font-mono font-bold text-neutral-600 w-7 pt-0.5 shrink-0">{layer.tag}</span>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-semibold text-white">{layer.name}</p>
                  <p className="text-xs text-neutral-500 mt-0.5">{layer.desc}</p>
                </div>
              </li>
            ))}
          </ol>

          <aside className="bg-white/[0.02] border border-white/5 rounded-xl p-5 space-y-4">
            <p className="text-[10px] font-mono uppercase tracking-widest text-neutral-500">stack</p>
            {STACK.map(({ kind, items }) => (
              <div key={kind}>
                <p className="text-[10px] font-mono text-neutral-600 mb-1.5">{kind}</p>
                <div className="flex flex-wrap gap-1">
                  {items.map((s) => (
                    <span key={s} className="text-[10px] font-mono text-neutral-300 px-1.5 py-0.5 rounded border border-white/10 bg-white/[0.02]">
                      {s}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </aside>
        </div>
      </section>

      {/* ── License & contribution ─────────────────────────────────────── */}
      <section className="space-y-4">
        <header className="space-y-1">
          <p className="text-[10px] font-mono uppercase tracking-widest text-neutral-600">## get involved</p>
          <h2 className="text-2xl font-black text-white tracking-tight">Use it. Read it. Send a patch.</h2>
        </header>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="bg-white/[0.02] border border-white/5 rounded-xl p-5">
            <div className="flex items-center gap-2 mb-2">
              <Scale size={14} className="text-neutral-400" aria-hidden="true" />
              <p className="text-xs font-bold text-white uppercase tracking-wider">License</p>
            </div>
            <p className="text-sm text-neutral-200">Apache 2.0</p>
            <p className="text-xs text-neutral-500 mt-1 leading-relaxed">
              Commercial use, modification, distribution, patent grant. No royalty. No vendor lock.
            </p>
          </div>
          <div className="bg-white/[0.02] border border-white/5 rounded-xl p-5">
            <div className="flex items-center gap-2 mb-2">
              <Package size={14} className="text-neutral-400" aria-hidden="true" />
              <p className="text-xs font-bold text-white uppercase tracking-wider">Self-host</p>
            </div>
            <code className="block text-[11px] font-mono text-blue-300 bg-blue-500/[0.06] border border-blue-500/15 rounded px-2 py-1 mt-0.5 break-all">
              git clone {GH_REPO}.git && docker compose up
            </code>
            <p className="text-xs text-neutral-500 mt-2 leading-relaxed">
              Single-node Postgres + Redis + 12 services. Bring your own ALB. AWS Compose override included.
            </p>
          </div>
          <div className="bg-white/[0.02] border border-white/5 rounded-xl p-5">
            <div className="flex items-center gap-2 mb-2">
              <Users size={14} className="text-neutral-400" aria-hidden="true" />
              <p className="text-xs font-bold text-white uppercase tracking-wider">Contribute</p>
            </div>
            <p className="text-sm text-neutral-200">PRs welcome.</p>
            <p className="text-xs text-neutral-500 mt-1 leading-relaxed">
              Tests required for behavior changes. See{' '}
              <a href={`${GH_REPO}/blob/main/CONTRIBUTING.md`} target="_blank" rel="noopener noreferrer" className="text-blue-300 hover:underline">CONTRIBUTING.md</a>.
              Security: <a href={`${GH_REPO}/blob/main/SECURITY.md`} target="_blank" rel="noopener noreferrer" className="text-blue-300 hover:underline">SECURITY.md</a>.
            </p>
          </div>
        </div>
      </section>

      {/* ── What's in the box ──────────────────────────────────────────── */}
      <section className="space-y-4">
        <header className="space-y-1">
          <p className="text-[10px] font-mono uppercase tracking-widest text-neutral-600">## what's in the box</p>
          <h2 className="text-2xl font-black text-white tracking-tight">Zero hidden enterprise features.</h2>
          <p className="text-sm text-neutral-500 max-w-2xl pt-1">
            Everything that runs at{' '}
            <code className="text-blue-300 font-mono text-[12px] px-1 py-0.5 rounded bg-blue-500/[0.06] border border-blue-500/15">aegisagent.in</code>{' '}
            is in the repo. No closed-source tier. No "Pro plan."
          </p>
        </header>

        <ul className="grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-2">
          {FEATURES.map((line) => (
            <li key={line} className="flex items-start gap-2 text-sm text-neutral-300">
              <CheckCircle2 size={13} className="text-green-400 shrink-0 mt-0.5" aria-hidden="true" />
              <span className="leading-snug">{line}</span>
            </li>
          ))}
        </ul>
      </section>

      {/* ── Cite ───────────────────────────────────────────────────────── */}
      <section className="space-y-4">
        <header className="space-y-1">
          <p className="text-[10px] font-mono uppercase tracking-widest text-neutral-600">## cite</p>
          <h2 className="text-2xl font-black text-white tracking-tight">If you build on AgentControl.</h2>
        </header>
        <div className="rounded-xl border border-white/5 bg-black/40 px-4 py-3 overflow-x-auto">
          <pre className="text-[11px] font-mono text-neutral-400 leading-relaxed whitespace-pre">
{`@software{agentcontrol2026,
  title    = {AgentControl: Open-source runtime governance for AI agents},
  author   = {Mishra, Abhishek},
  year     = {2026},
  url      = {${GH_REPO}},
  version  = {4.4.0},
  license  = {Apache-2.0}
}`}
          </pre>
        </div>
      </section>

      {/* ── Closing ────────────────────────────────────────────────────── */}
      <section className="rounded-2xl border border-white/10 bg-gradient-to-br from-white/[0.04] via-transparent to-transparent p-8 text-center space-y-5">
        <Award size={28} className="text-neutral-400 mx-auto" aria-hidden="true" />
        <h2 className="text-2xl sm:text-3xl font-black text-white">
          Free as in <span className="italic font-serif">infrastructure</span>.
        </h2>
        <p className="text-sm text-neutral-400 max-w-xl mx-auto leading-relaxed">
          Not "open core". Not "community edition". Every line that runs in production is in the repo,
          under Apache 2.0. The audit chain, the policy engine, the SSE bus, the receipts SDK — all of it.
        </p>
        <div className="flex flex-wrap items-center justify-center gap-3 pt-2">
          <a
            href={GH_REPO}
            target="_blank" rel="noopener noreferrer"
            className="inline-flex items-center gap-2 h-10 px-4 rounded-lg bg-white text-black text-xs font-bold uppercase tracking-wider hover:bg-neutral-200 transition-colors"
          >
            <Star size={13} aria-hidden="true" /> Star on GitHub
          </a>
          <a
            href={`${GH_REPO}/fork`}
            target="_blank" rel="noopener noreferrer"
            className="inline-flex items-center gap-2 h-10 px-4 rounded-lg border border-white/15 text-white text-xs font-bold uppercase tracking-wider hover:border-white/40 transition-colors"
          >
            <GitFork size={13} aria-hidden="true" /> Fork
          </a>
        </div>
        <p className="text-[10px] font-mono text-neutral-600 pt-3 tracking-wider">
          Apache 2.0 · v4.4.0 · ap-south-1 · {new Date().getFullYear()}
        </p>
      </section>
    </div>
  )
}
