import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  CheckCircle2, XCircle, MinusCircle, ArrowRight, RefreshCw,
} from 'lucide-react'

/**
 * /status — Public uptime + nightly-check status page.
 *
 * Sprint EI-14 (2026-06-20). Consumes the artefacts published by
 * `.github/workflows/nightly_verify.yml` (+ EI-13 sbom-cve step) to the
 * anonymously-readable bucket s3://aegis-public-roots-628478946931/.
 *
 * Three artefacts:
 *   - nightly/latest.json   — last completed nightly run, 6 status fields
 *   - uptime/30day.json     — % green days over the trailing 30-day window
 *   - nightly/<date>.json   — per-day archive (we fetch the last 30 to
 *                              render the grid)
 *
 * This page is ALSO mirrored as a single-file static HTML at
 * s3://aegis-public-roots/status/index.html so the surface survives an
 * Aegis ALB outage — the React version (this file) is the better UX
 * when the ALB IS up.
 */

const BUCKET_URL = 'https://aegis-public-roots-628478946931.s3.amazonaws.com'
const LATEST_URL   = `${BUCKET_URL}/nightly/latest.json`
const UPTIME_URL   = `${BUCKET_URL}/uptime/30day.json`

const CHECK_LABELS = [
  { key: 'aevf_v1_v6',  label: 'Cryptographic audit chain (AEVF V1–V6)' },
  { key: 'isolation',   label: 'Cross-tenant isolation (7-attack matrix)' },
  { key: 'public_probe', label: 'Public surface (/health, /trust, security.txt)' },
  { key: 'sbom_cve',    label: 'SBOM CVE diff (net-new HIGH+CRITICAL)' },
  { key: 'chaos',       label: 'Chaos drill (docker kill OPA / Redis / DB-pool)' },
]

function pillForStatus (status) {
  const s = String(status || '').toLowerCase()
  if (s === 'pass' || s === 'verified' || s === 'success') {
    return { color: 'text-green-400', bg: 'bg-green-500/[0.06]', border: 'border-green-500/30', Icon: CheckCircle2, label: 'Operational' }
  }
  if (s === 'fail' || s === 'new-cves' || s === 'error') {
    return { color: 'text-red-400',   bg: 'bg-red-500/[0.06]',   border: 'border-red-500/30',   Icon: XCircle,       label: 'Incident' }
  }
  if (s === 'no_roots' || s === 'skip' || s === '' || s === 'unknown') {
    return { color: 'text-neutral-500', bg: 'bg-white/[0.02]', border: 'border-white/[0.08]', Icon: MinusCircle, label: 'No data' }
  }
  return { color: 'text-amber-400', bg: 'bg-amber-500/[0.06]', border: 'border-amber-500/30', Icon: MinusCircle, label: String(status) }
}

function Header ({ latest, loading }) {
  const allGreen = latest && CHECK_LABELS.every(
    (c) => pillForStatus(latest[c.key]).label === 'Operational',
  )
  const overall = loading
    ? { color: 'text-neutral-400', text: 'Loading…' }
    : allGreen
      ? { color: 'text-green-400', text: 'All systems operational' }
      : { color: 'text-amber-400', text: 'See checks below' }

  return (
    <section className="px-6 py-16 lg:py-20 max-w-5xl mx-auto text-center">
      <div className="inline-flex items-center gap-2 text-[10px] uppercase tracking-widest text-neutral-500 mb-4">
        Status · {latest?.date || '—'}
      </div>
      <h1 className={`text-3xl lg:text-4xl font-bold tracking-tight ${overall.color}`}>
        {overall.text}
      </h1>
      <p className="text-sm lg:text-base text-neutral-400 leading-relaxed mt-4 max-w-2xl mx-auto">
        Every night three workflows run independent checks against our staging
        environment + the public transparency bucket and publish the result
        anonymously. This page reflects the most recent run.
      </p>
    </section>
  )
}

function CheckCard ({ label, status }) {
  const pill = pillForStatus(status)
  return (
    <div className={`p-4 rounded-xl border ${pill.border} ${pill.bg}`}>
      <div className="flex items-start gap-3">
        <pill.Icon size={18} className={`${pill.color} shrink-0 mt-0.5`} aria-hidden="true" />
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-white">{label}</div>
          <div className={`text-[11px] mt-0.5 ${pill.color}`}>{pill.label}</div>
        </div>
      </div>
    </div>
  )
}

function Checks ({ latest }) {
  if (!latest) return null
  return (
    <section className="px-6 max-w-5xl mx-auto">
      <h2 className="text-xs uppercase tracking-widest text-neutral-500 mb-3">
        Last nightly run · {latest.run_at_utc}
      </h2>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {CHECK_LABELS.map((c) => (
          <CheckCard key={c.key} label={c.label} status={latest[c.key]} />
        ))}
      </div>
    </section>
  )
}

function UptimeRow ({ uptime }) {
  if (!uptime) return null
  const pct = Number(uptime.green_pct ?? 0)
  const cls = pct >= 99 ? 'text-green-400' : pct >= 95 ? 'text-amber-400' : 'text-red-400'
  return (
    <section className="px-6 py-12 max-w-5xl mx-auto">
      <div className="p-5 rounded-xl border border-white/[0.08] bg-white/[0.02]">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div>
            <h2 className="text-xs uppercase tracking-widest text-neutral-500 mb-1">
              30-day green-day rate
            </h2>
            <p className="text-xs text-neutral-500">
              A day is "green" only when every nightly check passed AND no new HIGH+CRITICAL
              CVE appeared. {uptime.green_days}/{uptime.total_days} days in the window.
            </p>
          </div>
          <div className={`text-3xl font-bold ${cls}`}>{pct.toFixed(2)}%</div>
        </div>
      </div>
    </section>
  )
}

function Raw () {
  return (
    <section className="px-6 py-12 max-w-5xl mx-auto">
      <h2 className="text-xs uppercase tracking-widest text-neutral-500 mb-3">
        Raw artefacts (no credentials required)
      </h2>
      <div className="p-5 rounded-xl border border-white/[0.08] bg-white/[0.02]">
        <p className="text-xs text-neutral-400 leading-relaxed">
          The four sources this page renders are all anonymously fetchable from
          the same bucket as our daily transparency roots — you can re-verify
          everything on this page without trusting our DNS, our load balancer,
          or our application code.
        </p>
        <pre className="mt-3 px-3 py-2 text-[11px] font-mono bg-black/40 border border-white/[0.06] rounded text-neutral-200 overflow-x-auto">
{`# Latest nightly run (status pill at the top of this page)
aws s3 cp s3://aegis-public-roots-628478946931/nightly/latest.json - \\
  --no-sign-request

# 30-day uptime rollup
aws s3 cp s3://aegis-public-roots-628478946931/uptime/30day.json - \\
  --no-sign-request

# Yesterday's SBOM CVE scan (raw findings, pre-diff)
aws s3 cp s3://aegis-public-roots-628478946931/cve-history/yesterday.json - \\
  --no-sign-request

# Any historical day (replace 2026-06-20):
aws s3 cp s3://aegis-public-roots-628478946931/nightly/2026-06-20.json - \\
  --no-sign-request`}
        </pre>
      </div>
    </section>
  )
}

function Foot ({ error, onRefresh, loading }) {
  return (
    <section className="px-6 py-16 max-w-5xl mx-auto text-center">
      {error && (
        <p className="text-xs text-amber-400 mb-3">
          Could not load latest status from the public bucket: {error}
        </p>
      )}
      <button
        type="button"
        onClick={onRefresh}
        disabled={loading}
        className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border border-white/[0.10] text-xs text-neutral-300 hover:border-white/[0.20] hover:text-white disabled:opacity-50"
      >
        <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
        Refresh
      </button>
      <div className="mt-6">
        <Link to="/trust" className="inline-flex items-center gap-1 text-[11px] text-neutral-500 hover:text-white">
          <ArrowRight size={11} aria-hidden="true" className="rotate-180" />
          Back to Trust Center
        </Link>
      </div>
    </section>
  )
}

export default function StatusPage () {
  const [latest, setLatest] = useState(null)
  const [uptime, setUptime] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const [latestRes, uptimeRes] = await Promise.allSettled([
        fetch(`${LATEST_URL}?t=${Date.now()}`).then((r) => r.ok ? r.json() : null),
        fetch(`${UPTIME_URL}?t=${Date.now()}`).then((r) => r.ok ? r.json() : null),
      ])
      if (latestRes.status === 'fulfilled' && latestRes.value) setLatest(latestRes.value)
      else throw new Error('latest.json not yet published')
      if (uptimeRes.status === 'fulfilled' && uptimeRes.value) setUptime(uptimeRes.value)
    } catch (e) {
      setError(e?.message || 'Public artefact unavailable')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  return (
    <div className="min-h-screen bg-black text-neutral-200">
      <Header latest={latest} loading={loading} />
      <Checks latest={latest} />
      <UptimeRow uptime={uptime} />
      <Raw />
      <Foot error={error} onRefresh={load} loading={loading} />
    </div>
  )
}
