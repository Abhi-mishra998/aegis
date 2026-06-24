import React, { useState, useEffect, useCallback, useRef } from 'react'
import {
  CheckCircle,
  AlertTriangle,
  XCircle,
  RefreshCw,
  Server,
  Clock,
  Activity,
  Database,
  Layers,
  HeartPulse,
} from 'lucide-react'
import { dashboardService } from '../services/api'
import SkeletonLoader from '../components/Common/SkeletonLoader'

// Queue depth thresholds — keep in sync with infra/prometheus-rules.yml.
// audit_stream crit matches: max_over_time(acp_audit_stream_length[1m]) > 45000
// DLQ thresholds are depth guards (Alertmanager fires on age > 60s, not depth,
// but we surface depth as the visible UI signal for on-call engineers).
const QUEUE_THRESHOLDS = {
  // The audit stream is a debug ring buffer capped by the producer at
  // MAXLEN=10K (`sdk/common/audit_stream.py`). Steady-state fill at
  // ~10K is NORMAL; only an overflow above the cap (Redis approximate
  // trim slack) is interesting. Treat 12K as warn, 15K as crit.
  AUDIT_STREAM_WARN: 12_000,
  AUDIT_STREAM_CRIT: 15_000,
  // Consumer lag is the REAL "is the audit pipeline keeping up" signal —
  // count of un-ACKed entries the consumer hasn't processed yet.
  // services/audit/main.py logs a warning at lag > 1000, so we warn
  // earlier (500) and crit at the consumer's own warn threshold.
  AUDIT_CONSUMER_LAG_WARN:  500,
  AUDIT_CONSUMER_LAG_CRIT: 1_000,
  AUDIT_DLQ_WARN:       1,
  AUDIT_DLQ_CRIT:      50,
  BILLING_RETRY_WARN:   1,
  BILLING_RETRY_CRIT:  50,
  BILLING_DLQ_WARN:     1,
  BILLING_DLQ_CRIT:    20,
}

const STATUS_CONFIG = {
  healthy:     { icon: CheckCircle,   color: 'text-green-400',  bg: 'bg-green-500/10',  border: 'border-green-500/20',  label: 'Healthy' },
  degraded:    { icon: AlertTriangle, color: 'text-yellow-400', bg: 'bg-yellow-500/10', border: 'border-yellow-500/20', label: 'Degraded' },
  unreachable: { icon: XCircle,       color: 'text-red-400',    bg: 'bg-red-500/10',    border: 'border-red-500/20',    label: 'Unreachable' },
  unknown:     { icon: Activity,      color: 'text-neutral-400',bg: 'bg-neutral-500/10',border: 'border-neutral-500/20',label: 'Unknown' },
}

// 2026-05-14 — 4-state classification aligned with gateway /system/health.
// Distinguishes queue/latency pressure (`degraded_performance`) from actual
// service outage (`partial_outage` / `major_outage`). Back-compat: older
// payloads with `healthy` / `degraded` / `down` still render correctly.
const OVERALL_CONFIG = {
  operational:          { color: 'text-green-400',  bg: 'bg-green-500/10',  border: 'border-green-500/30',  label: 'All Systems Operational' },
  degraded_performance: { color: 'text-amber-400',  bg: 'bg-amber-500/10',  border: 'border-amber-500/30',  label: 'Degraded Performance' },
  partial_outage:       { color: 'text-orange-400', bg: 'bg-orange-500/10', border: 'border-orange-500/30', label: 'Partial Outage' },
  major_outage:         { color: 'text-red-400',    bg: 'bg-red-500/10',    border: 'border-red-500/30',    label: 'Major Outage' },
  // legacy aliases
  healthy:              { color: 'text-green-400',  bg: 'bg-green-500/10',  border: 'border-green-500/30',  label: 'All Systems Operational' },
  degraded:             { color: 'text-amber-400',  bg: 'bg-amber-500/10',  border: 'border-amber-500/30',  label: 'Degraded Performance' },
  down:                 { color: 'text-red-400',    bg: 'bg-red-500/10',    border: 'border-red-500/30',    label: 'Major Outage' },
  unknown:              { color: 'text-neutral-400',bg: 'bg-neutral-500/10',border: 'border-neutral-500/20',label: 'Checking...' },
}

function ServiceCard({ name, info }) {
  const cfg = STATUS_CONFIG[info?.status] || STATUS_CONFIG.unknown
  const Icon = cfg.icon
  const latency = info?.latency_ms

  return (
    <div className={`rounded-xl border ${cfg.border} ${cfg.bg} p-4 flex flex-col gap-3`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-white/5 flex items-center justify-center">
            <Server size={15} className="text-neutral-400" />
          </div>
          <span className="text-sm font-semibold text-white capitalize">{name}</span>
        </div>
        <div className={`flex items-center gap-1.5 ${cfg.color}`}>
          <Icon size={14} />
          <span className="text-xs font-medium">{cfg.label}</span>
        </div>
      </div>

      <div className="flex items-center gap-4 text-xs text-neutral-500">
        {latency !== undefined && (
          <span className="flex items-center gap-1">
            <Clock size={11} />
            {latency}ms
          </span>
        )}
        {info?.error && (
          <span className="text-red-400/80 truncate">{info.error}</span>
        )}
      </div>

      {latency !== undefined && (
        <div className="h-1 rounded-full bg-white/5 overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-700 ${
              latency < 100 ? 'bg-green-500' :
              latency < 300 ? 'bg-yellow-500' : 'bg-red-500'
            }`}
            style={{ width: `${Math.min(100, (latency / 500) * 100)}%` }}
          />
        </div>
      )}
    </div>
  )
}

function OverallBanner({ status, healthy, total, lastChecked }) {
  const cfg = OVERALL_CONFIG[status] || OVERALL_CONFIG.unknown
  const Icon =
    status === 'operational' || status === 'healthy'
      ? CheckCircle
      : status === 'degraded_performance' || status === 'degraded' || status === 'partial_outage'
      ? AlertTriangle
      : XCircle

  return (
    <div className={`rounded-2xl border ${cfg.border} ${cfg.bg} p-6 flex items-center justify-between gap-4 flex-wrap`}>
      <div className="flex items-center gap-4 min-w-0">
        <div className={`w-12 h-12 rounded-xl flex items-center justify-center ${cfg.bg} border ${cfg.border} shrink-0`}>
          <Icon size={22} className={cfg.color} />
        </div>
        <div className="min-w-0">
          <p className={`text-lg font-bold ${cfg.color} truncate`}>{cfg.label}</p>
          <p className="text-sm text-neutral-400 mt-0.5">
            {healthy}/{total} services operational
          </p>
        </div>
      </div>
      {lastChecked && (
        <p className="text-xs text-neutral-600 font-mono shrink-0">
          Last checked {new Date(lastChecked * 1000).toLocaleTimeString()}
        </p>
      )}
    </div>
  )
}

export default function SystemHealth() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [refreshing, setRefreshing] = useState(false)
  const intervalRef = useRef(null)
  // First load = full skeleton; subsequent SSE/poll refetches swap data silently.
  const hasLoadedRef = useRef(false)

  const fetchHealth = useCallback(async (isManual = false) => {
    if (isManual) setRefreshing(true)
    else if (!hasLoadedRef.current) setLoading(true)

    try {
      const res = await dashboardService.getSystemHealth()
      // Gateway returns either a flat health object or wraps it under `.data`.
      // Unwrap defensively so this page does not silently render empty cards.
      setData(res?.data ?? res ?? {})
      setError(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
      setRefreshing(false)
      hasLoadedRef.current = true
    }
  }, [])

  useEffect(() => {
    fetchHealth()
    intervalRef.current = setInterval(() => fetchHealth(), 30_000)
    return () => clearInterval(intervalRef.current)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const services = data?.services || {}
  const gateway = data?.gateway || null
  const queues = data?.queues || {}

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div className="page-header">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight">System Health</h1>
          <p className="text-xs text-neutral-500 mt-1">
            Live health status for all ACP backend services
          </p>
        </div>
        <button
          onClick={() => fetchHealth(true)}
          disabled={refreshing}
          className="flex items-center gap-2 px-3 py-2 rounded-lg bg-white/5 border border-white/10
                     text-xs text-neutral-300 hover:text-white hover:bg-white/10 transition-colors disabled:opacity-50"
        >
          <RefreshCw size={13} className={refreshing ? 'animate-spin' : ''} />
          {refreshing ? 'Checking...' : 'Refresh'}
        </button>
      </div>

      {/* Overall Banner */}
      {loading && !data ? (
        <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-6 animate-pulse h-24"
             role="status" aria-label="Loading system health…" />
      ) : error ? (
        <div className="rounded-2xl border border-red-500/20 bg-red-500/5 p-6 flex items-start justify-between gap-4 flex-wrap"
             role="alert">
          <div className="flex items-start gap-3">
            <XCircle size={20} className="text-red-400 shrink-0 mt-0.5" aria-hidden="true" />
            <div className="space-y-1">
              <p className="text-sm font-semibold text-red-300">Failed to fetch health</p>
              <p className="text-xs text-red-400/80 break-words">{error}</p>
              <p className="text-xs text-neutral-500">
                The gateway /system/health endpoint did not respond. Retry below,
                or check the deployment if this persists.
              </p>
            </div>
          </div>
          <button
            onClick={() => fetchHealth(true)}
            disabled={refreshing}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/30
                       text-xs text-red-200 hover:text-white hover:bg-red-500/20 transition-colors disabled:opacity-50 shrink-0"
          >
            <RefreshCw size={13} className={refreshing ? 'animate-spin' : ''} />
            Retry
          </button>
        </div>
      ) : (
        <OverallBanner
          status={data?.status || 'unknown'}
          healthy={data?.healthy ?? 0}
          total={data?.total ?? 0}
          lastChecked={data?.ts}
        />
      )}

      {/* Service Grid */}
      {loading && !data ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          <SkeletonLoader variant="card" count={9} />
        </div>
      ) : !error && !gateway && Object.keys(services).length === 0 ? (
        <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-8 text-center flex flex-col items-center gap-3">
          <HeartPulse size={28} className="text-neutral-500" aria-hidden="true" />
          <div className="text-sm font-semibold text-white">No services reported</div>
          <div className="text-xs text-neutral-500 max-w-md">
            /system/health returned an empty payload. This usually means the
            gateway booted without registering any downstream probes. Refresh
            once the platform has been up for at least 30 seconds.
          </div>
          <button
            onClick={() => fetchHealth(true)}
            disabled={refreshing}
            className="mt-1 flex items-center gap-2 px-3 py-2 rounded-lg bg-white/5 border border-white/10
                       text-xs text-neutral-200 hover:text-white hover:bg-white/10 transition-colors disabled:opacity-50"
          >
            <RefreshCw size={13} className={refreshing ? 'animate-spin' : ''} />
            {refreshing ? 'Checking…' : 'Re-check now'}
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
          {gateway && <ServiceCard name="gateway" info={gateway} />}
          {Object.entries(services).map(([name, info]) => (
            <ServiceCard key={name} name={name} info={info} />
          ))}
        </div>
      )}

      {/* Operational Queues (2026-05-13): audit stream, audit DLQ, billing retry / DLQ */}
      {data && (
        <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-5">
          <div className="flex items-center gap-2 mb-4">
            <Layers size={14} className="text-neutral-400" />
            <span className="text-sm font-semibold text-white">Operational Queues</span>
            <span className="ml-auto text-[10px] font-mono text-neutral-600 hidden sm:inline">
              from /system/health
            </span>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-5 gap-3">
            <QueueTile
              label="Audit Ring Buffer"
              value={queues.audit_stream_length ?? 0}
              warn={QUEUE_THRESHOLDS.AUDIT_STREAM_WARN}
              crit={QUEUE_THRESHOLDS.AUDIT_STREAM_CRIT}
              hint="debug-only — capped at MAXLEN=10K; Postgres is the source of truth"
            />
            <QueueTile
              label="Consumer Lag"
              value={queues.audit_consumer_lag ?? 0}
              warn={QUEUE_THRESHOLDS.AUDIT_CONSUMER_LAG_WARN}
              crit={QUEUE_THRESHOLDS.AUDIT_CONSUMER_LAG_CRIT}
              hint="un-ACKed audit events — the real backlog signal"
            />
            <QueueTile
              label="Audit DLQ"
              value={queues.audit_dlq_length ?? 0}
              warn={QUEUE_THRESHOLDS.AUDIT_DLQ_WARN}
              crit={QUEUE_THRESHOLDS.AUDIT_DLQ_CRIT}
              hint="terminal-failed audit events"
            />
            <QueueTile
              label="Billing Retry Queue"
              value={queues.billing_retry_queue ?? 0}
              warn={QUEUE_THRESHOLDS.BILLING_RETRY_WARN}
              crit={QUEUE_THRESHOLDS.BILLING_RETRY_CRIT}
              hint="failed billing events awaiting heal"
            />
            <QueueTile
              label="Billing DLQ"
              value={queues.billing_dlq_length ?? 0}
              warn={QUEUE_THRESHOLDS.BILLING_DLQ_WARN}
              crit={QUEUE_THRESHOLDS.BILLING_DLQ_CRIT}
              hint="exhausted retries — manual review"
            />
          </div>
        </div>
      )}

      {/* Audit Pipeline Health — replay worker + lifetime durability */}
      {data && (
        <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-5">
          <div className="flex items-center gap-2 mb-4">
            <Activity size={14} className="text-neutral-400" />
            <span className="text-sm font-semibold text-white">Audit Pipeline Health</span>
            <span className="ml-auto text-[10px] font-mono text-neutral-600">
              since-deploy totals from /system/health
            </span>
          </div>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <QueueTile
              label="Audit Success Rate"
              value={queues.audit_success_rate_pct ?? 100}
              warn={99.9}
              crit={99}
              hint="persisted / (persisted + dlq_landings)"
              isRate
              invert
            />
            <QueueTile
              label="Replay Success Rate"
              value={queues.audit_dlq_replay_success_rate_pct ?? 100}
              warn={95}
              crit={90}
              hint="replayed / (replayed + permanently_failed)"
              isRate
              invert
            />
            <QueueTile
              label="DLQ Pending"
              value={queues.audit_dlq_length ?? 0}
              warn={QUEUE_THRESHOLDS.AUDIT_DLQ_WARN}
              crit={QUEUE_THRESHOLDS.AUDIT_DLQ_CRIT}
              hint="awaiting replay worker (60s tick)"
            />
            <QueueTile
              label="Permanently Failed"
              value={queues.audit_permanently_failed_length ?? 0}
              warn={1}
              crit={50}
              hint="non-recoverable — see runbook"
            />
          </div>
        </div>
      )}

      {/* Billing Pipeline Health — same shape, mirrors billing replay worker */}
      {data && (
        <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-5">
          <div className="flex items-center gap-2 mb-4">
            <Activity size={14} className="text-neutral-400" />
            <span className="text-sm font-semibold text-white">Billing Pipeline Health</span>
            <span className="ml-auto text-[10px] font-mono text-neutral-600">
              since-deploy totals from /system/health
            </span>
          </div>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <QueueTile
              label="Billing Success Rate"
              value={queues.billing_success_rate_pct ?? 100}
              warn={99.9}
              crit={99}
              hint="(attempted - failed) / attempted"
              isRate
              invert
            />
            <QueueTile
              label="Replay Success Rate"
              value={queues.billing_dlq_replay_success_rate_pct ?? 100}
              warn={95}
              crit={90}
              hint="replayed / (replayed + permanently_failed)"
              isRate
              invert
            />
            <QueueTile
              label="DLQ Pending"
              value={queues.billing_dlq_length ?? 0}
              warn={QUEUE_THRESHOLDS.BILLING_DLQ_WARN}
              crit={QUEUE_THRESHOLDS.BILLING_DLQ_CRIT}
              hint="awaiting billing replay worker (60s tick)"
            />
            <QueueTile
              label="Permanently Failed"
              value={queues.billing_permanently_failed_length ?? 0}
              warn={1}
              crit={50}
              hint="non-recoverable — see billing-dlq runbook"
            />
          </div>
        </div>
      )}

      {/* Auto-refresh note */}
      <p className="text-xs text-neutral-700 text-center">
        Auto-refreshes every 30 seconds
      </p>
    </div>
  )
}

function QueueTile({ label, value, warn, crit, hint, isRate = false, invert = false }) {
  // Default (depth): higher = worse — `value >= crit` is bad.
  // Inverted (success rate): lower = worse — `value <= crit` is bad.
  const status = invert
    ? (value <= crit ? 'crit' : value <= warn ? 'warn' : 'ok')
    : (value >= crit ? 'crit' : value >= warn ? 'warn' : 'ok')
  const cls = {
    ok:   { text: 'text-green-400',  border: 'border-green-500/15', bg: 'bg-green-500/5' },
    warn: { text: 'text-yellow-300', border: 'border-yellow-500/25', bg: 'bg-yellow-500/5' },
    crit: { text: 'text-red-400',    border: 'border-red-500/30',   bg: 'bg-red-500/10' },
  }[status]
  const display = isRate
    ? `${Number(value).toFixed(2)}%`
    : Number(value).toLocaleString()
  return (
    <div className={`rounded-xl border ${cls.border} ${cls.bg} p-3 flex flex-col gap-1.5`}>
      <div className="flex items-center gap-2">
        <Database size={11} className="text-neutral-500" />
        <span className="text-[10px] font-mono uppercase tracking-widest text-neutral-500 truncate">{label}</span>
      </div>
      <span className={`text-xl font-bold font-mono tabular-nums ${cls.text}`}>
        {display}
      </span>
      {hint && <p className="text-[10px] text-neutral-600 truncate">{hint}</p>}
    </div>
  )
}
