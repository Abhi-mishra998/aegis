import React from 'react'
import { Link } from 'react-router-dom'
import {
  Shield, ShieldCheck, Lock, FileBadge2, Globe, Activity,
  ArrowRight, FileText, ExternalLink, CheckCircle2, GitBranch, MapPin,
} from 'lucide-react'

/**
 * /trust — Public marketing trust center.
 *
 * Sprint EH-6. The architect's #3 finding was "no trust center page —
 * customers have nowhere to go when they ask 'is this secure?'." This
 * is that page. It is intentionally:
 *   - Public (no auth gate).
 *   - Linked to the supporting docs in the repo (not stored inline) so
 *     a single source of truth lives in docs/security/*.
 *   - Conservative in claims — every assertion has a code citation or
 *     a runbook link, and we never claim a certification we don't hold.
 */

const SECTIONS = [
  {
    icon: ShieldCheck,
    title: 'Tenant isolation',
    body: 'Every SQL query carries WHERE tenant_id = $1. The gateway rejects spoofed X-Tenant-ID headers with 403. Independently verified by 7/7 cross-tenant attack vectors in our isolation pen-test.',
    href: 'https://github.com/Abhi-mishra998/aegis/blob/main/reports/e2e_test_2026_06_20/isolation_test.sh',
    linkLabel: 'Pen-test script',
  },
  {
    icon: Lock,
    title: 'Encryption',
    body: 'TLS 1.2+ in transit; AES-256 at rest in RDS, ElastiCache, and S3. Per-tenant KMS CMK for the audit envelope. Cryptographic keys stored in AWS Secrets Manager with CloudTrail access logging.',
    href: 'https://github.com/Abhi-mishra998/aegis/blob/main/docs/security/data_classification.md',
    linkLabel: 'Data classification',
  },
  {
    icon: FileBadge2,
    title: 'Cryptographic transparency',
    body: 'Every decision is signed with ed25519. Daily Merkle roots are published to a public S3 bucket. Customers can verify months of audit log history offline with our aegis-verify CLI without trusting us.',
    href: 'https://github.com/Abhi-mishra998/aegis/blob/main/docs/AEVF/README.md',
    linkLabel: 'AEVF verification spec',
  },
  {
    icon: Shield,
    title: 'RBAC matrix',
    body: 'Every authenticated route is mapped to an allowed role set. 77 unit tests + a centralized enforcement layer in services/gateway/_rbac_map.py. A DEVELOPER token cannot call /compliance/export.',
    href: 'https://github.com/Abhi-mishra998/aegis/blob/main/docs/security/rbac_matrix.md',
    linkLabel: 'RBAC matrix doc',
  },
  {
    icon: GitBranch,
    title: 'Supply chain',
    body: 'Every PR runs Trivy + Gitleaks + Checkov + Bandit. Release bundles signed with cosign keyless OIDC; EC2 hosts refuse to extract an unsigned bundle. CODEOWNERS-gated reviews + required signed commits.',
    href: 'https://github.com/Abhi-mishra998/aegis/blob/main/docs/security/git_hardening.md',
    linkLabel: 'Git hardening runbook',
  },
  {
    icon: Activity,
    title: 'Operational monitoring',
    body: 'Prometheus + AlertManager with security-specific counters (auth failures, tenant-isolation violations, RBAC denials, mass exports, revoked token storms). Public status page (this site /status) backed by the nightly verify workflow + a static S3 mirror at status.aegisagent.in for outage-time availability.',
    href: '/status',
    linkLabel: 'Live status page',
  },
  {
    icon: FileText,
    title: 'Disaster recovery',
    body: 'RDS Multi-AZ + nightly age-encrypted backups + cross-region snapshot copy. RTO < 1 hour for full data loss. Monthly restore drills logged in dr_drill_log.md.',
    href: 'https://github.com/Abhi-mishra998/aegis/blob/main/docs/runbooks/disaster_recovery.md',
    linkLabel: 'DR runbook',
  },
  {
    icon: Globe,
    title: 'Subprocessors',
    body: 'Seven vendors: AWS, Anthropic, OpenAI, Clerk, Stripe, GitHub, Sigstore. Each listed with purpose, data shared, region, and compliance attestations. 30-day notice on any new vendor with new data class.',
    href: 'https://github.com/Abhi-mishra998/aegis/blob/main/docs/security/subprocessors.md',
    linkLabel: 'Subprocessor list',
  },
  {
    icon: MapPin,
    title: 'Data residency',
    body: 'Default region: ap-south-1 (Mumbai). Dedicated eu-west-1 (Ireland) instance available on contract for EU customers — tenant runtime data never leaves the chosen region. Per-data-class residency table maps every artifact to its region of record.',
    href: 'https://github.com/Abhi-mishra998/aegis/blob/main/docs/security/data_residency.md',
    linkLabel: 'Per-data-class residency table',
  },
]

const COMPLIANCE = [
  { name: 'SOC 2 Type I',  status: 'In progress (Q3 2026)', achieved: false },
  { name: 'SOC 2 Type II', status: 'Scheduled Q1 2027',     achieved: false },
  { name: 'EU AI Act Article 12 (audit-record minimum)', status: 'Code-compliant — AEVF spec maps each record',  achieved: true },
  { name: 'India DPDP Act Sec. 8(5) (record retention)',  status: 'Code-compliant — default 365-day retention',   achieved: true },
  { name: 'NIST AI RMF',     status: 'Mapped — see AEVF spec', achieved: true },
  { name: 'ISO 27001',       status: 'Roadmap Q4 2027',         achieved: false },
  { name: 'External pen test', status: 'Scheduled Q3 2026',     achieved: false },
]

const RESPONSIBLE_DISCLOSURE = (
  <>
    Found a vulnerability? Email{' '}
    <a className="text-white underline hover:text-neutral-200" href="mailto:security@aegisagent.in">
      security@aegisagent.in
    </a>{' '}
    or open a GitHub Security Advisory. We acknowledge within 48 hours, triage within 5 business days, and ship a fix for High/Critical within 90 days. See our signed{' '}
    <a className="text-white underline hover:text-neutral-200" href="/.well-known/security.txt">
      /.well-known/security.txt
    </a>
    .
  </>
)


function Header() {
  return (
    <section className="px-6 py-16 lg:py-20 max-w-5xl mx-auto text-center">
      <div className="inline-flex items-center gap-2 text-[10px] uppercase tracking-widest text-neutral-500 mb-4">
        <ShieldCheck size={11} aria-hidden="true" />
        Trust Center · v1.0 · 2026-06-21
      </div>
      <h1 className="text-3xl lg:text-4xl font-bold tracking-tight text-white">
        How Aegis treats your data.
      </h1>
      <p className="text-sm lg:text-base text-neutral-400 leading-relaxed mt-4 max-w-2xl mx-auto">
        Every claim on this page links to the corresponding code, runbook, or
        independent verification artifact. No marketing copy.
      </p>
    </section>
  )
}


function Section({ icon: Icon, title, body, href, linkLabel }) {
  return (
    <div className="p-5 rounded-xl border border-white/[0.08] bg-white/[0.02] hover:bg-white/[0.04] hover:border-white/[0.15] transition-colors">
      <div className="flex items-start gap-3">
        <div className="w-9 h-9 rounded-lg flex items-center justify-center bg-white/[0.04] border border-white/[0.06] shrink-0">
          <Icon size={16} className="text-white" aria-hidden="true" />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold text-white">{title}</h3>
          <p className="text-xs text-neutral-400 leading-relaxed mt-1.5">{body}</p>
          {href && (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-[11px] text-neutral-300 hover:text-white mt-3 underline-offset-2 hover:underline"
            >
              {linkLabel} <ExternalLink size={10} aria-hidden="true" />
            </a>
          )}
        </div>
      </div>
    </div>
  )
}


function Sections() {
  return (
    <section className="px-6 max-w-5xl mx-auto">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {SECTIONS.map((s) => <Section key={s.title} {...s} />)}
      </div>
    </section>
  )
}


function Compliance() {
  return (
    <section className="px-6 py-12 max-w-5xl mx-auto">
      <h2 className="text-xs uppercase tracking-widest text-neutral-500 mb-3">Compliance status</h2>
      <div className="rounded-xl border border-white/[0.08] bg-white/[0.02] divide-y divide-white/[0.05] overflow-hidden">
        {COMPLIANCE.map((c) => (
          <div key={c.name} className="flex items-center gap-3 px-4 py-3">
            {c.achieved
              ? <CheckCircle2 size={14} className="text-green-400 shrink-0" aria-hidden="true" />
              : <div className="w-3.5 h-3.5 rounded-full border border-neutral-600 shrink-0" aria-hidden="true" />
            }
            <span className="text-xs text-white flex-1">{c.name}</span>
            <span className={`text-[11px] ${c.achieved ? 'text-green-400/80' : 'text-neutral-500'}`}>
              {c.status}
            </span>
          </div>
        ))}
      </div>
      <p className="text-[10px] text-neutral-600 mt-3 leading-relaxed">
        Items marked "Code-compliant" are evidenced in the audit chain today; "In progress"
        means we are gathering the 90-day evidence the auditor requires.
      </p>
    </section>
  )
}


function Disclosure() {
  return (
    <section className="px-6 py-12 max-w-5xl mx-auto">
      <div className="p-5 rounded-xl border border-amber-500/20 bg-amber-500/[0.04]">
        <h2 className="text-xs uppercase tracking-widest text-amber-300/80 mb-2">Responsible disclosure</h2>
        <p className="text-sm text-neutral-200 leading-relaxed">
          {RESPONSIBLE_DISCLOSURE}
        </p>
      </div>
    </section>
  )
}


function NightlyEvidence() {
  return (
    <section className="px-6 py-12 max-w-5xl mx-auto">
      <h2 className="text-xs uppercase tracking-widest text-neutral-500 mb-3">Nightly evidence</h2>
      <div className="p-5 rounded-xl border border-white/[0.08] bg-white/[0.02]">
        <p className="text-sm text-neutral-200 leading-relaxed">
          Every night three GitHub Actions workflows run against staging in
          sequence (soak at 03:13, verify at 04:13, chaos at 05:13 UTC) and
          publish results to the same public bucket as our audit roots:
        </p>
        <ol className="mt-3 list-decimal pl-5 text-xs text-neutral-400 space-y-1.5 leading-relaxed">
          <li>100-user × 10-minute soak (locust); fails on &gt; 1% errors or p99 &gt; 5 s</li>
          <li>AEVF V1–V6 walk over every daily transparency root</li>
          <li>Cross-tenant isolation 7-attack matrix against staging.aegisagent.in</li>
          <li>Public-surface probe of /health, /trust, /.well-known/security.txt</li>
          <li>SBOM CVE diff: trivy on the latest CycloneDX SBOM <em>and</em> on every SHA256-pinned container image we ship, source-tagged + fails on any net-new HIGH+CRITICAL CVE since yesterday</li>
          <li>Chaos drill: docker kill OPA / policy / decision / Redis under live load + DB-pool burst</li>
        </ol>
        <p className="text-xs text-neutral-500 mt-4 leading-relaxed">
          Fetch the most recent run (no AWS credentials required):
        </p>
        <pre className="mt-2 px-3 py-2 text-[11px] font-mono bg-black/40 border border-white/[0.06] rounded text-neutral-200 overflow-x-auto">
{`aws s3 cp s3://aegis-public-roots-628478946931/nightly/latest.json - \\
  --no-sign-request`}
        </pre>
      </div>
    </section>
  )
}


function LegalTemplates() {
  return (
    <section className="px-6 py-12 max-w-5xl mx-auto">
      <h2 className="text-xs uppercase tracking-widest text-neutral-500 mb-3">Contract templates</h2>
      <div className="p-5 rounded-xl border border-white/[0.08] bg-white/[0.02]">
        <p className="text-sm text-neutral-200 leading-relaxed">
          Four templates ready for your counsel to redline:
        </p>
        <ul className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs text-neutral-400">
          <li><span className="text-neutral-300 font-semibold">MSA</span> — Master Service Agreement (16 sections)</li>
          <li><span className="text-neutral-300 font-semibold">DPA</span> — Data Processing Agreement (GDPR Art. 28, India DPDP §8)</li>
          <li><span className="text-neutral-300 font-semibold">BAA</span> — HIPAA Business Associate (Covered-Entity overlay)</li>
          <li><span className="text-neutral-300 font-semibold">SLA</span> — 99.5% Design-Partner / 99.9% Enterprise + service-credit schedule</li>
        </ul>
        <p className="text-xs text-neutral-500 mt-4 leading-relaxed">
          All four ship in <code className="text-neutral-300">12_legal/</code> of the Customer
          Security Package — request a copy below or via{' '}
          <a className="text-white underline hover:text-neutral-200"
             href="https://github.com/Abhi-mishra998/aegis/tree/main/docs/legal">
            docs/legal on GitHub
          </a>.
        </p>
      </div>
    </section>
  )
}


function Foot() {
  return (
    <section className="px-6 py-16 max-w-5xl mx-auto text-center">
      <p className="text-xs text-neutral-500">
        For a Vendor Security Questionnaire response or a one-shot ZIP of all of the above —{' '}
        <a className="text-white underline hover:text-neutral-200" href="mailto:security@aegisagent.in?subject=Customer Security Package request">
          email us
        </a>
        .
      </p>
      <div className="mt-6">
        <Link to="/" className="inline-flex items-center gap-1 text-[11px] text-neutral-500 hover:text-white">
          <ArrowRight size={11} aria-hidden="true" className="rotate-180" />
          Back to home
        </Link>
      </div>
    </section>
  )
}


export default function TrustCenter() {
  return (
    <div className="min-h-screen bg-black text-neutral-200">
      <Header />
      <Sections />
      <Compliance />
      <NightlyEvidence />
      <LegalTemplates />
      <Disclosure />
      <Foot />
    </div>
  )
}
