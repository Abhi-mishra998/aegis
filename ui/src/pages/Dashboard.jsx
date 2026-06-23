import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Activity,
  AlertTriangle,
  Bot,
  Brain,
  CheckCircle2,
  Clock,
  Code2,
  Cpu,
  DollarSign,
  FileCheck2,
  Globe,
  Plus,
  RefreshCw,
  Shield,
  ShieldCheck,
  Sparkles,
  Target,
  Terminal,
  TrendingUp,
  Users,
  Wand2,
} from 'lucide-react';
import {
  workspaceService,
  registryService,
  auditService,
  dashboardService,
} from '../services/api';
import { useSSE } from '../hooks/useSSE';
import { useAuth } from '../hooks/useAuth';
import Button from '../components/Common/Button';
import Card from '../components/Common/Card';
import SkeletonLoader from '../components/Common/SkeletonLoader';

const PROVIDER_META = {
  anthropic:   { label: 'Anthropic',   icon: Brain    },
  openai:      { label: 'OpenAI',      icon: Sparkles },
  bedrock:     { label: 'Bedrock',     icon: Cpu      },
  langchain:   { label: 'LangChain',   icon: Code2    },
  cursor:      { label: 'Cursor',      icon: Bot      },
  'claude-code': { label: 'Claude Code', icon: Terminal },
  openhands:   { label: 'OpenHands',   icon: Wand2    },
  custom:      { label: 'Custom',      icon: Globe    },
  unknown:     { label: 'Pre-wizard',  icon: Users    },
};

const RISK_TIER_META = {
  low:      { label: 'Low',      color: 'text-green-400',  bg: 'bg-green-500/[0.07]'  },
  medium:   { label: 'Medium',   color: 'text-amber-400',  bg: 'bg-amber-500/[0.07]'  },
  high:     { label: 'High',     color: 'text-orange-400', bg: 'bg-orange-500/[0.07]' },
  critical: { label: 'Critical', color: 'text-red-400',    bg: 'bg-red-500/[0.07]'    },
};

function MetricTile({ label, value, sublabel, accent = 'text-white', icon: Icon, tooltip, cta, pulseDot }) {
  return (
    <Card>
      <div className="relative space-y-1" title={tooltip || undefined}>
        {pulseDot && (
          <span
            aria-hidden="true"
            className="absolute -top-1 -right-1 flex h-2.5 w-2.5"
          >
            <span className="absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75 animate-ping" />
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-amber-400 animate-pulse" />
          </span>
        )}
        <div className="flex items-center gap-2 text-[10px] uppercase tracking-widest text-neutral-500">
          {Icon && <Icon size={11} aria-hidden="true" />}
          <span>{label}</span>
        </div>
        <div className={`text-3xl font-bold ${accent}`}>{value}</div>
        {sublabel && <div className="text-[11px] text-neutral-500">{sublabel}</div>}
        {cta && (
          <div className="pt-2">
            <span className="inline-flex items-center rounded-md bg-amber-500/15 px-2 py-1 text-[11px] font-semibold text-amber-300 ring-1 ring-inset ring-amber-500/30 hover:bg-amber-500/25 transition-colors">
              {cta}
            </span>
          </div>
        )}
      </div>
    </Card>
  );
}

// Compact skeleton for a single MetricTile while data is fetching — keeps the
// hero grid from showing a wall of "—" placeholders that look indistinguishable
// from a freshly seeded tenant with zero activity.
function MetricTileSkeleton() {
  return (
    <Card>
      <div className="space-y-2 animate-pulse" aria-hidden="true">
        <div className="h-2 w-16 bg-white/[0.06] rounded" />
        <div className="h-7 w-20 bg-white/[0.08] rounded" />
        <div className="h-2 w-24 bg-white/[0.04] rounded" />
      </div>
      <span className="sr-only">Loading metric…</span>
    </Card>
  );
}

// Sprint 12 — short integer formatter ("1.2K", "12.3M") so the mandate
// tiles stay legible even when a busy tenant racks up six-figure
// actions_evaluated counts.
function fmtInt(n) {
  if (n == null) return '—';
  const v = Number(n) || 0;
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000)     return `${(v / 1_000).toFixed(1)}K`;
  return v.toLocaleString();
}

function fmtUSD(n) {
  if (n == null) return '—';
  const v = Number(n) || 0;
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000)     return `$${(v / 1_000).toFixed(1)}K`;
  if (v >= 1)         return `$${v.toFixed(2)}`;
  if (v > 0)          return `$${v.toFixed(4)}`;
  return '$0';
}

// Empty-state replacement for the Agents tile on fresh tenants. Same footprint
// as MetricTile so the dashboard grid stays balanced.
function AgentsEmptyTile() {
  return (
    <Card>
      <div className="space-y-2">
        <div className="text-[10px] uppercase tracking-widest text-neutral-500">Agents</div>
        <div className="text-sm font-semibold text-white">No agents yet</div>
        <div className="text-[11px] text-neutral-500 leading-snug">
          Create your first agent to start governing tool calls.
        </div>
        <Link to="/onboarding" className="inline-block pt-1">
          <Button size="xs">
            Create agent
            <span aria-hidden="true">→</span>
          </Button>
        </Link>
      </div>
    </Card>
  );
}

function ProviderRow({ providerId, count, total }) {
  const meta = PROVIDER_META[providerId] || PROVIDER_META.custom;
  const Icon = meta.icon;
  const pct = total ? Math.round((count / total) * 100) : 0;
  return (
    <div className="flex items-center gap-3">
      <div className="w-7 h-7 rounded-md bg-white/[0.05] flex items-center justify-center text-neutral-300 shrink-0">
        <Icon size={14} aria-hidden="true" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline justify-between gap-3">
          <span className="text-sm font-medium text-neutral-200 truncate">{meta.label}</span>
          <span className="text-sm font-mono text-neutral-400">{count}</span>
        </div>
        <div className="h-1 mt-1.5 rounded-full bg-white/[0.04] overflow-hidden">
          <div className="h-full bg-white/30" style={{ width: `${pct}%` }} />
        </div>
      </div>
    </div>
  );
}

function RiskTile({ tier, count, total }) {
  const meta = RISK_TIER_META[tier] || RISK_TIER_META.low;
  const pct = total ? Math.round((count / total) * 100) : 0;
  return (
    <div className={`rounded-xl border border-white/[0.07] ${meta.bg} p-3 space-y-1`}>
      <div className={`text-[10px] uppercase tracking-widest ${meta.color}`}>{meta.label}</div>
      <div className="text-2xl font-bold text-white">{count}</div>
      <div className="text-[10px] text-neutral-500">{pct}% of fleet</div>
    </div>
  );
}

export default function Dashboard() {
  const { tenant_id } = useAuth();
  const [inventory, setInventory] = useState(null);
  const [workspace, setWorkspace] = useState(null);
  const [recentEvents, setRecentEvents] = useState([]);
  // Sprint 12 — mandate KPIs (6 metrics) + business-value (4 metrics).
  // Single fetch from the gateway aggregation endpoint so the hero row
  // renders with one round-trip alongside the existing inventory load.
  const [overview, setOverview] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [refreshTick, setRefreshTick] = useState(0);
  const [liveEventCount, setLiveEventCount] = useState(0);

  // Refetch when tenant_id changes (e.g., ClerkAuthBridge mirrors session
  // *after* this page mounted — without re-deriving on tenant_id, the user
  // sees a permanent "—" / "Failed to load" until they manually click
  // Refresh).
  useEffect(() => {
    if (!tenant_id) {
      // ProtectedRoute holds the bridge-syncing screen until tenant_id
      // arrives; in case a parent ever bypasses that gate, surface an
      // explanatory loading state rather than firing a doomed request.
      setLoading(true);
      return;
    }
    let cancelled = false;
    setLoading(true);
    Promise.allSettled([
      workspaceService.inventory(),
      workspaceService.me(),
      auditService.getLogs(10, 0),
      dashboardService.overview(),
    ]).then((results) => {
      if (cancelled) return;
      const [invResult, wsResult, evResult, ovResult] = results;
      const invResp = invResult.status === 'fulfilled' ? invResult.value : null;
      const wsResp  = wsResult.status  === 'fulfilled' ? wsResult.value  : null;
      const evResp  = evResult.status  === 'fulfilled' ? evResult.value  : null;
      const ovResp  = ovResult.status  === 'fulfilled' ? ovResult.value  : null;
      setInventory(invResp?.data || invResp || null);
      setWorkspace(wsResp?.data || wsResp || null);
      setOverview(ovResp?.data || ovResp || null);
      const items = evResp?.data?.items || evResp?.data || evResp?.items || [];
      setRecentEvents(Array.isArray(items) ? items.slice(0, 8) : []);
      setLoading(false);
      // Inventory + overview are the must-have fetches; treat failure as
      // total only if BOTH fail. Recent events + workspace identity can
      // degrade gracefully on a partial outage.
      const corePartiallyOk = (invResult.status === 'fulfilled')
        || (ovResult.status === 'fulfilled');
      if (!corePartiallyOk) {
        setError(invResult.reason?.message || ovResult.reason?.message || 'Failed to load dashboard data');
      } else {
        setError('');
      }
    });
    return () => { cancelled = true; };
  }, [refreshTick, tenant_id]);

  // Live tick on any SSE event so the operator sees the "Live" badge move.
  // SSE handshake needs the acp_token cookie set by /auth/clerk/provision;
  // gate on tenant_id so we don't open a doomed connection during the
  // sign-in bridge window.
  useSSE({
    enabled: Boolean(tenant_id),
    onMessage: (evt) => {
      if (!evt?.type) return;
      setLiveEventCount((c) => c + 1);
      // Sprint 20 UX pass — when a decision or override event lands,
      // re-pull the mandate KPIs so the Escalated tile + breakdown
      // refresh without the operator hitting Refresh.
      const t = String(evt.type).toLowerCase();
      if (t.includes('decision') || t.includes('override') || t.includes('approval') || t.includes('escalate')) {
        setRefreshTick((tick) => tick + 1);
      }
    },
  });

  // Sprint 20 UX pass — belt-and-braces poll every 20s in case SSE is
  // momentarily disconnected (rolling deploy, ALB drain, etc.). At
  // ~3 lightweight GETs per cycle (workspace, audit aggregate, overrides
  // join) this stays well under any per-tenant rate ceiling.
  useEffect(() => {
    if (!tenant_id) return;
    const id = setInterval(() => { setRefreshTick((t) => t + 1) }, 20_000);
    return () => clearInterval(id);
  }, [tenant_id]);

  const providerEntries = useMemo(() => {
    const by = inventory?.by_provider || {};
    return Object.entries(by)
      .filter(([, c]) => c > 0)
      .sort((a, b) => b[1] - a[1]);
  }, [inventory]);

  const riskEntries = useMemo(() => {
    const by = inventory?.by_risk || {};
    return ['critical', 'high', 'medium', 'low']
      .map((tier) => [tier, by[tier] || 0])
      // Always show medium even at zero so the tier-grid never collapses
      // to one card. Other tiers only render when populated.
      .filter(([t, c]) => c > 0 || t === 'medium');
  }, [inventory]);

  const shadowActive = !!workspace?.shadow_mode_active;
  const shadowDaysLeft = workspace?.shadow_mode_days_left;

  // "No activity yet" signal — every mandate KPI is zero/missing AND inventory
  // is also empty. Used to surface the seed-demo / onboarding CTA below the
  // hero KPI row instead of leaving the operator staring at six empty tiles.
  const heroAllZero = !loading
    && (overview?.mandate_kpis?.actions_evaluated ?? 0) === 0
    && (overview?.mandate_kpis?.allowed ?? 0) === 0
    && (overview?.mandate_kpis?.denied ?? 0) === 0
    && (overview?.mandate_kpis?.escalated ?? 0) === 0
    && (overview?.mandate_kpis?.active_findings ?? 0) === 0
    && (inventory?.total ?? 0) === 0;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold tracking-tight text-white">
            {workspace?.name || 'Workspace'} dashboard
          </h1>
          <p className="text-xs text-neutral-400 max-w-xl">
            Agent inventory, open incidents, and shadow-mode status — every metric
            your CISO asks for in a buyer demo, in one screen.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="text-[10px] uppercase tracking-widest text-neutral-600 flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" aria-hidden="true" />
            Live · {liveEventCount} events
          </div>
          <Button variant="ghost" size="sm" onClick={() => setRefreshTick((t) => t + 1)}>
            <RefreshCw size={14} aria-hidden="true" />
          </Button>
          <Link to="/onboarding">
            <Button size="sm">
              <Plus size={14} aria-hidden="true" />
              Add agent
            </Button>
          </Link>
        </div>
      </div>

      {error && (
        <div className="error-banner" role="alert">
          <div className="flex items-center gap-3">
            <AlertTriangle size={15} className="text-red-400 shrink-0" aria-hidden="true" />
            <p className="text-xs text-red-400">{error}</p>
          </div>
        </div>
      )}

      {/* Sprint 12 — Row 1: the six mandate KPIs every CISO buyer
          evaluates Aegis against. Numbers are 30-day totals from the
          audit log (allowed/denied/escalated/active_findings) plus the
          live agent count (protected_agents) — single fetch via the
          gateway /dashboard/overview aggregator. */}
      <div>
        <div className="text-[10px] uppercase tracking-widest text-neutral-500 mb-2 flex items-center gap-2">
          <Shield size={11} aria-hidden="true" />
          <span>Last 30 days · runtime security at a glance</span>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-3 xl:grid-cols-6 gap-3">
          {loading ? (
            Array.from({ length: 6 }).map((_, i) => <MetricTileSkeleton key={i} />)
          ) : (
            <>
              <MetricTile
                icon={Bot}
                label="Protected agents"
                value={fmtInt(overview?.mandate_kpis?.protected_agents)}
                sublabel="Active in this workspace"
                tooltip="Active agents (status=ACTIVE) registered in this workspace. Source of truth: /workspace/inventory."
              />
              <MetricTile
                icon={Activity}
                label="Actions evaluated"
                value={fmtInt(overview?.mandate_kpis?.actions_evaluated)}
                sublabel="Every tool call + proxy call"
                tooltip="Total tool-call + LLM-proxy-call decisions Aegis evaluated in the last 30 days."
              />
              <MetricTile
                icon={CheckCircle2}
                label="Allowed"
                value={fmtInt(overview?.mandate_kpis?.allowed)}
                sublabel="No risk gate hit"
                accent="text-green-400"
                tooltip="Decisions returned allow — no signal, policy, or budget threshold tripped."
              />
              <MetricTile
                icon={Shield}
                label="Denied"
                value={fmtInt(overview?.mandate_kpis?.denied)}
                sublabel="Hard block fired"
                accent={(overview?.mandate_kpis?.denied ?? 0) > 0 ? 'text-red-400' : 'text-white'}
                tooltip="Decisions returned deny / block / kill — Aegis refused the action before it ran."
              />
              <Link to="/approval-inbox" className="contents">
                <MetricTile
                  icon={AlertTriangle}
                  label="Escalated"
                  value={fmtInt(overview?.mandate_kpis?.escalated)}
                  sublabel={
                    (overview?.mandate_kpis?.escalated ?? 0) === 0
                      ? 'No human-in-loop yet'
                      : `${overview?.escalation_breakdown?.pending ?? 0} pending · ${overview?.escalation_breakdown?.approved ?? 0} approved · ${overview?.escalation_breakdown?.rejected ?? 0} rejected`
                  }
                  accent={(overview?.escalation_breakdown?.pending ?? 0) > 0 ? 'text-amber-400' : 'text-white'}
                  tooltip="Decisions sent to a human reviewer. Sub-label splits the total into pending (waiting on a human), approved (CFO/CISO/etc said yes), rejected (operator denied). Click to open the Approval Inbox."
                  pulseDot={(overview?.escalation_breakdown?.pending ?? 0) > 0}
                  cta={(overview?.escalation_breakdown?.pending ?? 0) > 0 ? 'Review →' : undefined}
                />
              </Link>
              <MetricTile
                icon={Target}
                label="Active findings"
                value={fmtInt(overview?.mandate_kpis?.active_findings)}
                sublabel="Decisions with signals"
                accent={(overview?.mandate_kpis?.active_findings ?? 0) > 0 ? 'text-amber-400' : 'text-white'}
                tooltip="Audit rows carrying one or more security findings (signal_registry hits)."
              />
            </>
          )}
        </div>

        {/* Zero-activity hint — surfaced once the mandate KPIs come back empty
            AND inventory is empty. Gives the operator a single clear next step
            instead of staring at a wall of em-dashes. */}
        {heroAllZero && (
          <div className="mt-3 rounded-xl border border-white/[0.06] bg-white/[0.02] p-4 flex items-center justify-between gap-4 flex-wrap">
            <div className="space-y-0.5">
              <div className="text-sm font-semibold text-white">
                No activity yet
              </div>
              <div className="text-xs text-neutral-500">
                Register your first agent through the onboarding wizard to start
                generating decisions, then come back here for the live view.
              </div>
            </div>
            <Link to="/onboarding">
              <Button size="sm">
                <Plus size={14} aria-hidden="true" />
                Start onboarding wizard
              </Button>
            </Link>
          </div>
        )}
      </div>

      {/* Sprint 12 — Row 2: business-value rollup. Translates the
          security metrics into the language a CFO + CISO + GC can
          share. Dollar figure uses the Sprint 8 system_values map for
          blocked tool calls, plus a $0.05 conservative estimate per
          blocked LLM-proxy call. */}
      <div>
        <div className="text-[10px] uppercase tracking-widest text-neutral-500 mb-2 flex items-center gap-2">
          <TrendingUp size={11} aria-hidden="true" />
          <span>Business value · what Aegis saved you</span>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {loading ? (
            Array.from({ length: 4 }).map((_, i) => <MetricTileSkeleton key={i} />)
          ) : (
            <>
              <MetricTile
                icon={FileCheck2}
                label="Records protected"
                value={fmtInt(overview?.business_value?.records_protected_estimate)}
                sublabel="Bulk-PII / dump blocks"
                tooltip="Estimated row count Aegis prevented from leaving the workspace via blocked SQL dumps / bulk PII egress."
              />
              <MetricTile
                icon={AlertTriangle}
                label="Escalations prevented"
                value={fmtInt(overview?.business_value?.escalations_prevented)}
                sublabel="Sent to approval inbox"
                tooltip="Actions Aegis kicked to a human reviewer instead of letting the agent self-execute."
              />
              <MetricTile
                icon={ShieldCheck}
                label="Controls enforced"
                value={fmtInt(overview?.business_value?.compliance_controls_enforced)}
                sublabel="Distinct signal classes"
                tooltip="Distinct security-signal classes (Security:*, Compliance:*, etc.) that Aegis fired against in this window."
              />
              <MetricTile
                icon={DollarSign}
                label="Dollar risk mitigated"
                value={fmtUSD(overview?.business_value?.dollar_risk_mitigated_usd)}
                sublabel="Wire blocks + LLM blocks"
                accent={(overview?.business_value?.dollar_risk_mitigated_usd ?? 0) > 0 ? 'text-green-400' : 'text-white'}
                tooltip="Sum of (wire-transfer amounts on denied money movement) + ($0.05 × blocked LLM-proxy calls). Lower-bound estimate."
              />
            </>
          )}
        </div>
      </div>

      {/* Workspace status row — preserves the wizard / shadow-mode
          context that used to live in the top row. Same numbers,
          lower-priority placement now that the mandate KPIs occupy
          the hero. */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        {loading ? (
          Array.from({ length: 4 }).map((_, i) => <MetricTileSkeleton key={i} />)
        ) : (
          <>
            {!(inventory?.total > 0) ? (
              <AgentsEmptyTile />
            ) : (
              <MetricTile
                icon={Bot}
                label="Total agents"
                value={fmtInt(inventory?.total)}
                sublabel={`${inventory?.active ?? 0} active · ${inventory?.quarantined ?? 0} quarantined`}
              />
            )}
            <MetricTile
              icon={AlertTriangle}
              label="High risk"
              value={fmtInt(inventory?.high_risk)}
              sublabel={`${inventory?.by_risk?.critical ?? 0} critical · ${inventory?.by_risk?.high ?? 0} high`}
              accent={(inventory?.high_risk ?? 0) > 0 ? 'text-amber-400' : 'text-white'}
            />
            <MetricTile
              icon={Wand2}
              label="Wizard provisioned"
              value={fmtInt(inventory?.wizard_provisioned)}
              sublabel="Created via /onboarding"
            />
            <MetricTile
              icon={Shield}
              label="Shadow mode"
              value={shadowActive ? `${shadowDaysLeft ?? '?'}d` : 'OFF'}
              sublabel={shadowActive ? 'Observe-only window' : 'Enforce mode'}
              accent={shadowActive ? 'text-amber-400' : 'text-green-400'}
            />
          </>
        )}
      </div>

      {/* Inventory hero — provider + risk-tier breakdowns */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card title="By provider" icon={Cpu} className="lg:col-span-2">
          {loading ? (
            <SkeletonLoader variant="text" count={3} />
          ) : providerEntries.length === 0 ? (
            <div className="text-xs text-neutral-500 py-6 text-center space-y-3 flex flex-col items-center">
              <Bot size={24} className="text-neutral-600" aria-hidden="true" />
              <div className="text-neutral-300 font-medium">No agents registered yet</div>
              <div className="text-neutral-500 max-w-xs">
                Provider breakdown appears once you onboard your first agent.
              </div>
              <Link to="/onboarding">
                <Button size="sm">
                  <Plus size={14} aria-hidden="true" />
                  Start with the Onboarding Wizard
                </Button>
              </Link>
            </div>
          ) : (
            <div className="space-y-3">
              {providerEntries.map(([providerId, count]) => (
                <ProviderRow
                  key={providerId}
                  providerId={providerId}
                  count={count}
                  total={inventory?.total ?? 0}
                />
              ))}
            </div>
          )}
        </Card>

        <Card title="By risk tier" icon={Shield}>
          {loading ? (
            <div className="grid grid-cols-2 gap-2" aria-hidden="true">
              {Array.from({ length: 4 }).map((_, i) => (
                <div
                  key={i}
                  className="rounded-xl border border-white/[0.05] bg-white/[0.02] p-3 animate-pulse h-20"
                />
              ))}
            </div>
          ) : (inventory?.total ?? 0) === 0 ? (
            <div className="text-xs text-neutral-500 py-6 text-center space-y-2 flex flex-col items-center">
              <Shield size={20} className="text-neutral-600" aria-hidden="true" />
              <div className="text-neutral-300 font-medium">—</div>
              <div className="text-neutral-500 max-w-xs">
                No risk tiers yet. Register an agent to start scoring.
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-2">
              {riskEntries.map(([tier, count]) => (
                <RiskTile
                  key={tier}
                  tier={tier}
                  count={count}
                  total={inventory?.total ?? 0}
                />
              ))}
            </div>
          )}
        </Card>
      </div>

      {/* Recent activity */}
      <Card title="Recent activity" icon={Clock}>
        {loading ? (
          <SkeletonLoader variant="row" count={5} />
        ) : recentEvents.length === 0 ? (
          <div className="text-xs text-neutral-500 py-6 text-center flex flex-col items-center gap-3">
            <CheckCircle2 size={24} className="text-green-400/60" aria-hidden="true" />
            <div className="text-neutral-300 font-medium">No activity yet</div>
            <div className="text-neutral-500 max-w-sm">
              Decisions show up here in real time. Onboard an agent or open the
              live event feed to verify the SSE stream is connected.
            </div>
            <div className="flex items-center gap-2 pt-1">
              <Link to="/onboarding">
                <Button size="xs">
                  <Plus size={12} aria-hidden="true" />
                  Onboard agent
                </Button>
              </Link>
              <Link to="/live-feed">
                <Button size="xs" variant="ghost">
                  Open live feed
                </Button>
              </Link>
            </div>
          </div>
        ) : (
          <ul className="space-y-2">
            {recentEvents.map((e, idx) => (
              <li
                key={e.id || idx}
                className="flex items-start gap-3 text-xs text-neutral-300 border-b border-white/[0.04] last:border-b-0 py-2"
              >
                <span className="text-[10px] text-neutral-600 font-mono w-20 shrink-0">
                  {e.timestamp?.slice(11, 19) || e.created_at?.slice(11, 19) || '—'}
                </span>
                <span className="font-mono text-[10px] uppercase text-neutral-500 w-24 shrink-0 truncate">
                  {e.action || 'event'}
                </span>
                <span className="text-neutral-300 flex-1 truncate min-w-0">
                  {e.tool_name || e.reason || e.message || '—'}
                </span>
                <span className="text-[10px] font-mono text-neutral-600 truncate w-16 shrink-0 hidden sm:inline">
                  {(e.agent_id || '').slice(0, 8) || '—'}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
