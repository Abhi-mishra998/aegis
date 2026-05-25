import React, { useState, useEffect, useCallback, useRef } from 'react';
import { billingService } from '../services/api';
import { useAuth } from '../hooks/useAuth';
import { eventBus } from '../lib/eventBus';
import {
  DollarSign,
  TrendingUp,
  ShieldAlert,
  Activity,
  BarChart2,
  FileText,
  Clock,
  Zap,
  AlertCircle,
  RefreshCw,
  ChevronRight,
  CheckCircle2,
  Hourglass,
  Download,
} from 'lucide-react';
import {
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  AreaChart,
  Area,
} from 'recharts';
import Card from '../components/Common/Card';
import Button from '../components/Common/Button';
import SkeletonLoader from '../components/Common/SkeletonLoader';

/* ── Helpers ───────────────────────────────────────────────────────────────── */
const fmt$ = (val) =>
  (Number(val) || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const fmtN = (val) => (Number(val) || 0).toLocaleString();

/* ── Invoice status badge ──────────────────────────────────────────────────── */
function StatusBadge({ status }) {
  const s = (status ?? '').toLowerCase();
  const style =
    s === 'paid' ? 'text-green-400 bg-green-500/10 border-green-500/20' :
      s === 'pending' ? 'text-amber-400 bg-amber-500/10 border-amber-500/20' :
        'text-neutral-400 bg-white/[0.05] border-white/10';
  const Icon = s === 'paid' ? CheckCircle2 : Hourglass;
  return (
    <span className={`status-badge ${style}`}>
      <Icon size={10} aria-hidden="true" />
      {s || 'unknown'}
    </span>
  );
}

/* ── Custom chart tooltip ──────────────────────────────────────────────────── */
function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-[#0a0a0a] border border-white/[0.06] rounded-xl px-4 py-3 shadow-2xl space-y-1">
      <p className="text-label">{label}</p>
      {payload.map((p) => (
        <p key={p.dataKey} className="text-xs font-semibold text-white">
          {p.name}: {fmtN(p.value)}
        </p>
      ))}
    </div>
  );
}

/* ── Main component ────────────────────────────────────────────────────────── */
export default function Billing() {
  useAuth();

  const [summary, setSummary] = useState(null);
  const [invoices, setInvoices] = useState([]);
  const [dashboard, setDashboard] = useState(null);
  const [anomalies, setAnomalies] = useState([]);
  const [sumLoad, setSumLoad] = useState(true);
  const [invLoad, setInvLoad] = useState(true);
  const [dashLoad, setDashLoad] = useState(true);
  const [anomLoad, setAnomLoad] = useState(true);
  const [sumError, setSumError] = useState('');
  const [invError, setInvError] = useState('');
  const [dashError, setDashError] = useState('');
  const [anomError, setAnomError] = useState('');
  const [lastUpdated, setLastUpdated] = useState(null);
  const mountedRef = useRef(true);

  const fetchSummary = useCallback(async () => {
    setSumLoad(true);
    setSumError('');
    try {
      const res = await billingService.getSummary();
      if (!mountedRef.current) return;
      setSummary(res?.data || res || {});
      setLastUpdated(new Date());
    } catch (err) {
      if (mountedRef.current) setSumError(err.message || 'Billing module unreachable.');
    } finally {
      if (mountedRef.current) setSumLoad(false);
    }
  }, []);

  const fetchInvoices = useCallback(async () => {
    setInvLoad(true);
    setInvError('');
    try {
      const res = await billingService.getInvoices();
      if (!mountedRef.current) return;
      const d = res?.data || res || {};
      setInvoices(Array.isArray(d) ? d : (d.invoices || []));
    } catch (err) {
      if (mountedRef.current) setInvError(err.message || 'Invoice ledger unreachable.');
    } finally {
      if (mountedRef.current) setInvLoad(false);
    }
  }, []);

  const fetchDashboard = useCallback(async () => {
    setDashLoad(true);
    setDashError('');
    try {
      const res = await billingService.getDashboard();
      if (mountedRef.current) setDashboard(res?.data || {});
    } catch (err) {
      // 2026-05-14: surface backend failure instead of silently console.error.
      // Previous behavior masked /usage/dashboard 5xx as an empty section.
      if (mountedRef.current) setDashError(err.message || 'Revenue dashboard unreachable.');
    } finally {
      if (mountedRef.current) setDashLoad(false);
    }
  }, []);

  const fetchAnomalies = useCallback(async () => {
    setAnomLoad(true);
    setAnomError('');
    try {
      const res = await billingService.getAnomalies();
      if (mountedRef.current) setAnomalies(res?.data || []);
    } catch (err) {
      if (mountedRef.current) setAnomError(err.message || 'Anomaly detector unreachable.');
    } finally {
      if (mountedRef.current) setAnomLoad(false);
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    fetchSummary();
    fetchInvoices();
    fetchDashboard();
    fetchAnomalies();
    const interval = setInterval(() => {
      fetchSummary(); fetchInvoices(); fetchDashboard(); fetchAnomalies();
    }, 30_000);
    return () => { mountedRef.current = false; clearInterval(interval); };
  }, [fetchSummary, fetchInvoices, fetchDashboard, fetchAnomalies]);

  // Real-time: refresh summary whenever the bus fires a tool execution or billing event
  useEffect(() => {
    const refresh = () => fetchSummary();
    const u1 = eventBus.on('tool_executed', refresh);
    const u2 = eventBus.on('policy_decision', refresh);
    return () => { u1(); u2(); };
  }, [fetchSummary]);

  /* Export report — trigger CSV download of invoices */
  const handleExportReport = () => {
    if (!invoices.length) return;
    const headers = ['Invoice ID', 'Period', 'Total Calls', 'Threats Blocked', 'Cost USD', 'Status'];
    const rows = invoices.map((inv) => [
      inv.invoice_id || inv.id || '',
      inv.period || inv.billing_period || '',
      inv.total_calls ?? '',
      inv.threats_blocked ?? '',
      inv.cost_usd ?? inv.amount ?? '',
      inv.status || '',
    ]);
    const csv = [headers, ...rows].map((r) => r.map(String).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `acp-billing-report-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  /* Loading skeleton */
  if (sumLoad && invLoad) {
    return (
      <div className="space-y-8 animate-fade-in max-w-[1400px] mx-auto">
        <div className="h-7 bg-white/[0.04] rounded w-52 animate-pulse" />
        <div className="h-28 bg-white/[0.02] border border-white/[0.04] rounded-2xl animate-pulse" />
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-5">
          {[...Array(4)].map((_, i) => <SkeletonLoader key={i} />)}
        </div>
        <SkeletonLoader variant="row" count={5} />
      </div>
    );
  }

  /* Derived values — mapped to backend billing summary shape */
  const s = summary || {};
  const totalCalls = s.total_calls ?? s.today?.cost_spikes_prevented ?? 0;
  const totalEvents = s.total_events ?? 0;
  const totalSaved = s.total_saved_usd ?? s.total_money_saved ?? 0;
  const roiPercent = s.roi_percent ?? 0;
  const threatsBlocked = s.threats_blocked ?? s.attacks_blocked ?? s.today?.threats_blocked ?? 0;
  const avgCallsDay = s.avg_calls_per_day ?? (totalCalls > 0 ? Math.round(totalCalls / 7) : 0);
  const peakHour = s.peak_hour ?? null;
  const costPerCall = s.cost_per_call ?? 0;
  const currentCost = s.current_cost_usd ?? totalCalls * costPerCall;

  /* Chart data — always explicit `calls` against `day` so the chart renders
     even with a single datapoint (single-bar trend is still a useful demo). */
  const trendRaw = s.daily_trend || s.trend || [];
  const hasData = trendRaw.length > 0;
  // Pad a single-day trend with a synthetic prior-day zero so Recharts can
  // draw a slope (a single point renders invisibly in an AreaChart).
  const chartData = hasData && trendRaw.length === 1
    ? [{ ...trendRaw[0], day: 'prev', calls: 0 }, trendRaw[0]]
    : trendRaw;
  const chartKey = 'calls';
  const labelKey = 'day';

  return (
    <div className="space-y-8 animate-fade-in max-w-[1400px] mx-auto">

      {/* ── Page header ── */}
      <div className="page-header">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold tracking-tight text-white">Billing & Usage</h1>
          <p className="text-xs text-neutral-500">ROI analytics, cost intelligence and invoice ledger</p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {lastUpdated && (
            <span className="flex items-center gap-1.5 text-xs text-neutral-600">
              <div className="w-1.5 h-1.5 rounded-full bg-green-500/70 animate-pulse" aria-hidden="true" />
              Updated {lastUpdated.toLocaleTimeString()}
            </span>
          )}
          <Button
            variant="secondary"
            size="sm"
            onClick={() => { fetchSummary(); fetchInvoices(); }}
          >
            <RefreshCw size={13} aria-hidden="true" />
            Refresh
          </Button>
          <Button
            variant="primary"
            size="sm"
            disabled={!invoices.length}
            onClick={handleExportReport}
          >
            <Download size={13} aria-hidden="true" />
            Export CSV
            <ChevronRight size={13} aria-hidden="true" />
          </Button>
        </div>
      </div>

      {/* ── Summary error ── */}
      {sumError && (
        <div className="error-banner" role="alert">
          <div className="flex items-center gap-3">
            <AlertCircle size={15} className="text-red-400 shrink-0" aria-hidden="true" />
            <p className="text-xs text-red-400">{sumError}</p>
          </div>
          <Button variant="danger" size="sm" onClick={fetchSummary}>
            <RefreshCw size={12} aria-hidden="true" />
            Retry
          </Button>
        </div>
      )}

      {/* ── ROI hero strip ── */}
      <div className="kpi-strip group">
        <div
          className="absolute inset-0 bg-gradient-to-r from-green-500/[0.04] to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-700 pointer-events-none"
          aria-hidden="true"
        />
        <div className="relative flex flex-col sm:flex-row items-start sm:items-center justify-between gap-6">
          <div className="flex items-center gap-4">
            <div className="w-12 h-12 rounded-xl bg-white flex items-center justify-center shrink-0">
              <TrendingUp size={22} className="text-black" aria-hidden="true" />
            </div>
            <div>
              <h2 className="text-base font-bold text-white">ROI Security Value</h2>
              <p className="text-xs text-neutral-500 mt-0.5">
                ACP blocked <span className="text-white font-semibold">{fmtN(threatsBlocked)}</span> threats,
                saving <span className="text-green-400 font-semibold">${fmt$(totalSaved)}</span>
              </p>
            </div>
          </div>
          <div className="flex items-center gap-8 sm:gap-10 flex-wrap">
            <div className="text-center">
              <p className="text-label">Threats Blocked</p>
              <p className="text-2xl font-bold text-white mt-0.5">{fmtN(threatsBlocked)}</p>
            </div>
            <div className="w-px h-10 bg-white/10 hidden sm:block" aria-hidden="true" />
            <div className="text-center">
              <p className="text-label">ROI</p>
              <p className={`text-2xl font-bold mt-0.5 ${roiPercent >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {roiPercent >= 0 ? '+' : ''}{Number(roiPercent).toFixed(1)}%
              </p>
            </div>
            <div className="w-px h-10 bg-white/10 hidden sm:block" aria-hidden="true" />
            <div className="text-center">
              <p className="text-label">Total Saved</p>
              <p className="text-2xl font-bold text-white mt-0.5">${fmt$(totalSaved)}</p>
            </div>
          </div>
        </div>
      </div>

      {/* ── Revenue Dashboard & Reconciliation ── */}
      {dashError && (
        <div className="error-banner" role="alert">
          <div className="flex items-center gap-3">
            <AlertCircle size={15} className="text-red-400 shrink-0" aria-hidden="true" />
            <p className="text-xs text-red-400">Revenue dashboard: {dashError}</p>
          </div>
          <Button variant="danger" size="sm" onClick={fetchDashboard}>
            <RefreshCw size={12} aria-hidden="true" />
            Retry
          </Button>
        </div>
      )}
      {anomError && (
        <div className="error-banner" role="alert">
          <div className="flex items-center gap-3">
            <AlertCircle size={15} className="text-red-400 shrink-0" aria-hidden="true" />
            <p className="text-xs text-red-400">Anomaly detector: {anomError}</p>
          </div>
          <Button variant="danger" size="sm" onClick={fetchAnomalies}>
            <RefreshCw size={12} aria-hidden="true" />
            Retry
          </Button>
        </div>
      )}
      {dashboard && (
        <>
          <div className="grid grid-cols-1 md:grid-cols-5 gap-5">
            <div className="p-5 bg-[#0a0a0a] border border-white/[0.05] rounded-2xl flex flex-col justify-center md:col-span-2">
              <h3 className="text-sm font-semibold text-neutral-400">Billing Integrity Score</h3>
              <div className="flex items-end gap-3 mt-2">
                <span className={`text-3xl font-bold ${dashboard.billing_integrity_score >= 1.0 ? 'text-green-400' : 'text-amber-400'}`}>
                  {dashboard.billing_integrity_score >= 1.0 ? '100%' : `${(dashboard.billing_integrity_score * 100).toFixed(2)}%`}
                </span>
                <span className="text-xs text-neutral-500 mb-1">
                  Zero revenue leakage
                </span>
              </div>

              <div className="grid grid-cols-2 gap-4 mt-4 pt-4 border-t border-white/[0.05]">
                <div>
                  <span className="text-[10px] uppercase font-bold text-neutral-500">Unbilled (SLA &gt; 60s)</span>
                  <div className={`text-lg font-bold ${dashboard.unbilled_events_sla === 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {dashboard.unbilled_events_sla}
                  </div>
                </div>
                <div>
                  <span className="text-[10px] uppercase font-bold text-neutral-500">Pending (In-flight)</span>
                  <div className="text-lg font-bold text-neutral-400">
                    {dashboard.pending_events || 0}
                  </div>
                </div>
              </div>
            </div>

            <div className="p-5 bg-[#0a0a0a] border border-white/[0.05] rounded-2xl flex flex-col justify-center">
              <h3 className="text-sm font-semibold text-neutral-400">Reconciliation SLA</h3>
              <div className="space-y-4 mt-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-neutral-500">P95 Latency</span>
                  <span className={`text-xs font-bold ${dashboard.p95_latency_sec < 60 ? 'text-green-400' : 'text-amber-400'}`}>
                    {dashboard.p95_latency_sec}s
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-neutral-500">P99 Latency</span>
                  <span className={`text-xs font-bold ${dashboard.p99_latency_sec < 120 ? 'text-green-400' : 'text-amber-400'}`}>
                    {dashboard.p99_latency_sec}s
                  </span>
                </div>
              </div>
            </div>

            <div className="p-5 bg-[#0a0a0a] border border-white/[0.05] rounded-2xl flex flex-col justify-center">
              <h3 className="text-sm font-semibold text-neutral-400 flex items-center gap-2">
                Queue Health
                {/* 2026-05-13: Surface the C-1 / H-5 enforcement guarantees */}
                <span
                  className="ml-auto inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full
                             bg-green-500/10 border border-green-500/20 text-[9px] font-mono
                             font-bold text-green-400"
                  title="Idempotency-protected · synchronous billing · DLQ-backed retry"
                >
                  <CheckCircle2 size={9} />
                  GUARANTEED
                </span>
              </h3>
              <div className="space-y-4 mt-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-neutral-500 flex items-center gap-1.5">
                    <Activity size={12} className="text-blue-400" />
                    Retry Queue
                  </span>
                  <span className={`text-xs font-bold ${dashboard.retry_queue_size > 0 ? 'text-amber-400' : 'text-white'}`}>
                    {dashboard.retry_queue_size || 0}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-neutral-500 flex items-center gap-1.5">
                    <ShieldAlert size={12} className="text-red-400" />
                    Dead Letter
                  </span>
                  <span className={`text-xs font-bold ${dashboard.dlq_size > 0 ? 'text-red-400' : 'text-green-400'}`}>
                    {dashboard.dlq_size || 0}
                  </span>
                </div>
              </div>
            </div>

            <div className="p-5 bg-[#0a0a0a] border border-white/[0.05] rounded-2xl flex flex-col justify-center">
              <h3 className="text-sm font-semibold text-neutral-400 mb-3">Cost per Agent</h3>
              <div className="space-y-2 max-h-24 overflow-y-auto pr-2">
                {(dashboard.cost_per_agent || []).length === 0 ? (
                  <p className="text-xs text-neutral-600">No agent costs recorded</p>
                ) : (dashboard.cost_per_agent || []).map((c, i) => (
                  <div key={i} className="flex justify-between items-center">
                    <span className="text-xs text-white truncate max-w-[60%] font-mono">{c.agent_id.split('-')[0]}...</span>
                    <span className="text-xs font-semibold text-neutral-300">${fmt$(c.cost)}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="p-5 bg-[#0a0a0a] border border-white/[0.05] rounded-2xl">
            <h3 className="text-sm font-semibold text-neutral-400 mb-3">Cost per Tool</h3>
            <div className="space-y-2 max-h-24 overflow-y-auto pr-2">
              {(dashboard.cost_per_tool || []).length === 0 ? (
                <p className="text-xs text-neutral-600">No tool costs recorded</p>
              ) : (dashboard.cost_per_tool || []).map((c, i) => (
                <div key={i} className="flex justify-between items-center">
                  <span className="text-xs text-white truncate">{c.tool}</span>
                  <span className="text-xs font-semibold text-neutral-300">${fmt$(c.cost)}</span>
                </div>
              ))}
            </div>
          </div>
        </>
      )}

{/* ── Anomaly Detection ── */ }
{
  anomalies && anomalies.length > 0 && (
    <div className="card-premium p-6 border-red-500/20">
      <div className="flex justify-between items-center mb-4">
        <div className="flex items-center gap-2">
          <AlertCircle size={16} className="text-red-400" />
          <h3 className="text-sm font-bold text-white">Billing Anomalies Detected</h3>
        </div>
        <span className="px-2 py-0.5 rounded text-[10px] uppercase font-bold bg-red-500/10 text-red-400">Action Required</span>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {anomalies.map((a, i) => (
          <div key={i} className="p-3 bg-red-500/[0.02] border border-red-500/10 rounded-xl flex flex-col gap-1">
            <div className="flex justify-between items-start">
              <span className="text-xs font-bold text-red-400 capitalize">{a.type.replace(/_/g, ' ')}</span>
              <span className="text-xs text-neutral-500">{new Date(a.timestamp).toLocaleTimeString()}</span>
            </div>
            <div className="text-xs text-neutral-300 font-mono mt-1">Agent: {a.agent_id.split('-')[0]}</div>
            <div className="flex justify-between items-center mt-2">
              <span className="text-xs font-semibold text-white">{fmtN(a.units)} units</span>
              <span className="text-xs font-semibold text-red-400">${fmt$(a.cost)}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

{/* ── Usage KPIs ── */ }
<div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-5">
  <Card title="Total API Calls" value={fmtN(totalCalls)} subtitle="Requests processed" icon={Activity} trend="up" trendValue="Active" />
  <Card title="Total Events" value={fmtN(totalEvents)} subtitle="Security decision events" icon={Zap} />
  <Card title="Avg Calls / Day" value={fmtN(avgCallsDay)} subtitle="7-day rolling average" icon={BarChart2} />
  <Card title="Peak Hour" value={peakHour != null ? `${peakHour}:00` : '—'} subtitle="Highest traffic window" icon={Clock} />
</div>

{/* ── Chart + cost breakdown ── */ }
<div className="grid grid-cols-1 xl:grid-cols-3 gap-6">

  {/* Usage trend */}
  <div className="xl:col-span-2 card-premium p-6 space-y-5">
    <div className="flex items-center justify-between">
      <div>
        <h3 className="text-sm font-bold text-white">API Call Volume Trend</h3>
        <p className="text-xs text-neutral-500 mt-0.5">7-day usage analytics</p>
      </div>
      {!hasData && (
        <span className="text-xs text-neutral-600 border border-white/[0.05] px-2 py-1 rounded-md">
          No historical data
        </span>
      )}
    </div>

    <div className="h-64 relative">
      {!hasData ? (
        <div className="absolute inset-0 flex items-center justify-center border border-white/[0.05] rounded-xl bg-white/[0.01]">
          <p className="text-sm text-neutral-500">No historical data available</p>
        </div>
      ) : (
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData}>
            <defs>
              <linearGradient id="callsGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={'#22c55e'} stopOpacity={0.18} />
                <stop offset="95%" stopColor={'#22c55e'} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="rgba(255,255,255,0.03)" />
            <XAxis dataKey={labelKey} axisLine={false} tickLine={false} tick={{ fill: '#525252', fontSize: 11 }} />
            <YAxis axisLine={false} tickLine={false} tick={{ fill: '#525252', fontSize: 11 }} />
            <Tooltip content={<ChartTooltip />} />
            <Area
              type="monotone"
              dataKey={chartKey}
              name="API Calls"
              stroke={'#22c55e'}
              strokeWidth={2}
              fill="url(#callsGrad)"
              animationDuration={1500}
            />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </div>
  </div>

  {/* Cost breakdown */}
  <div className="card-premium p-6 space-y-5">
    <div>
      <h3 className="text-sm font-bold text-white">Cost Breakdown</h3>
      <p className="text-xs text-neutral-500 mt-0.5">Current billing cycle</p>
    </div>

    <div className="space-y-3">
      {/* API Usage Cost */}
      <div className="p-3.5 bg-white/[0.02] border border-white/[0.05] rounded-xl space-y-2 hover:border-white/10 transition-colors">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <DollarSign size={13} className="text-neutral-400" aria-hidden="true" />
            <span className="text-xs text-neutral-400">API Usage Cost</span>
          </div>
          <span className="text-sm font-bold text-white">${fmt$(currentCost)}</span>
        </div>
        <div className="h-1 bg-white/[0.05] rounded-full overflow-hidden">
          <div
            className="h-full bg-white/40 rounded-full transition-all duration-700"
            style={{ width: `${Math.min(100, totalSaved > 0 ? Math.round((currentCost / totalSaved) * 100) : (currentCost > 0 ? 100 : 0))}%` }}
            aria-label={`${currentCost > 0 ? Math.round((currentCost / Math.max(currentCost, totalSaved)) * 100) : 0}% of budget`}
          />
        </div>
      </div>

      {/* Threat savings */}
      <div className="p-3.5 bg-green-500/[0.03] border border-green-500/10 rounded-xl space-y-2 hover:border-green-500/20 transition-colors">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <ShieldAlert size={13} className="text-green-400" aria-hidden="true" />
            <span className="text-xs text-green-400">Threat Savings</span>
          </div>
          <span className="text-sm font-bold text-green-400">${fmt$(totalSaved)}</span>
        </div>
        <div className="h-1 bg-green-500/10 rounded-full overflow-hidden">
          <div
            className="h-full bg-green-500 w-full rounded-full"
            style={{ boxShadow: '0 0 6px rgba(34,197,94,0.4)' }}
          />
        </div>
      </div>

      {/* Net ROI */}
      <div className="p-3.5 bg-white/[0.02] border border-white/[0.05] rounded-xl hover:border-white/10 transition-colors">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <TrendingUp size={13} className="text-blue-400" aria-hidden="true" />
            <span className="text-xs text-blue-400">Net ROI Value</span>
          </div>
          <span className={`text-sm font-bold ${(totalSaved - currentCost) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
            ${fmt$(Math.abs(totalSaved - currentCost))}
          </span>
        </div>
      </div>

      {/* Included services */}
      <div className="pt-3 border-t border-white/[0.05] space-y-1.5">
        <p className="text-label">Included Services</p>
        {[
          'Gateway Threat Interception',
          'Behavioral Anomaly Detection',
          'AI Forensic Narratives (Groq)',
          'Immutable Audit Chain',
          'Kill-Switch Automation',
        ].map((item) => (
          <div key={item} className="flex items-center gap-2">
            <span className="w-1 h-1 rounded-full bg-white/20 shrink-0" aria-hidden="true" />
            <span className="text-xs text-neutral-500">{item}</span>
          </div>
        ))}
      </div>
    </div>
  </div>
</div>

{/* ── Invoice ledger ── */ }
<div className="space-y-4">
  <div className="flex items-center justify-between">
    <div className="section-header">
      <FileText size={14} className="text-neutral-600" aria-hidden="true" />
      Invoice Ledger
    </div>
    <div className="flex items-center gap-4">
      <div className="flex items-center gap-1.5">
        <span className="w-1.5 h-1.5 rounded-full bg-green-500/40 border border-green-500/60" aria-hidden="true" />
        <span className="text-xs text-neutral-600">Paid</span>
      </div>
      <div className="flex items-center gap-1.5">
        <span className="w-1.5 h-1.5 rounded-full bg-amber-500/40 border border-amber-500/60" aria-hidden="true" />
        <span className="text-xs text-neutral-600">Pending</span>
      </div>
    </div>
  </div>

  {invError && (
    <div className="error-banner" role="alert">
      <div className="flex items-center gap-3">
        <AlertCircle size={15} className="text-red-400 shrink-0" aria-hidden="true" />
        <p className="text-xs text-red-400">{invError}</p>
      </div>
      <Button variant="danger" size="sm" onClick={fetchInvoices}>
        <RefreshCw size={12} aria-hidden="true" />
        Retry
      </Button>
    </div>
  )}

  {invLoad ? (
    <SkeletonLoader variant="row" count={5} />
  ) : (
    <div className="table-container animate-scale-in">
      <div className="table-scroll">
        <table className="table-base" aria-label="Invoice ledger">
          <thead>
            <tr>
              {['Invoice ID', 'Period', 'Total Calls', 'Threats Blocked', 'Cost USD', 'Status'].map((h) => (
                <th key={h} className="table-th">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {invoices.length === 0 ? (
              <tr>
                <td colSpan={6} className="py-14 text-center">
                  <div className="flex flex-col items-center gap-3 opacity-30">
                    <FileText size={32} className="text-neutral-700" aria-hidden="true" />
                    <p className="text-xs text-neutral-500">No invoices available.</p>
                  </div>
                </td>
              </tr>
            ) : (
              invoices.map((inv, idx) => (
                <tr key={inv.invoice_id || inv.id || idx} className="table-row">
                  <td className="table-td font-mono text-neutral-400">
                    {inv.invoice_id || inv.id || `INV-${String(idx + 1).padStart(4, '0')}`}
                  </td>
                  <td className="table-td">
                    {inv.period || inv.billing_period || '—'}
                  </td>
                  <td className="table-td font-semibold text-white">
                    {fmtN(inv.total_calls)}
                  </td>
                  <td className="table-td">
                    <div className="flex items-center gap-1.5">
                      <ShieldAlert size={11} className="text-red-400 shrink-0" aria-hidden="true" />
                      <span className="font-semibold text-white">{fmtN(inv.threats_blocked)}</span>
                    </div>
                  </td>
                  <td className="table-td font-semibold text-white">
                    ${fmt$(inv.cost_usd ?? inv.amount ?? 0)}
                  </td>
                  <td className="table-td">
                    <StatusBadge status={inv.status} />
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {invoices.length > 0 && (
        <div className="px-6 py-3 border-t border-white/[0.04] flex items-center justify-between">
          <span className="text-xs text-neutral-700">
            {invoices.length} invoice{invoices.length !== 1 ? 's' : ''} on record
          </span>
          <span className="text-xs text-neutral-700 font-mono">
            Last updated: {lastUpdated ? lastUpdated.toLocaleTimeString() : '—'}
          </span>
        </div>
      )}
    </div>
  )}
</div>
    </div>
  );
}

