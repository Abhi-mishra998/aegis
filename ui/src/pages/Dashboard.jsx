import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  AlertTriangle,
  Bot,
  Brain,
  CheckCircle2,
  Clock,
  Code2,
  Cpu,
  Globe,
  Plus,
  RefreshCw,
  Shield,
  Sparkles,
  Terminal,
  Users,
  Wand2,
} from 'lucide-react';
import {
  workspaceService,
  registryService,
  auditService,
} from '../services/api';
import { useSSE } from '../hooks/useSSE';
import Button from '../components/Common/Button';
import Card from '../components/Common/Card';

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

function MetricTile({ label, value, sublabel, accent = 'text-white' }) {
  return (
    <Card>
      <div className="space-y-1">
        <div className="text-[10px] uppercase tracking-widest text-neutral-500">{label}</div>
        <div className={`text-3xl font-bold ${accent}`}>{value}</div>
        {sublabel && <div className="text-[11px] text-neutral-500">{sublabel}</div>}
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
  const [inventory, setInventory] = useState(null);
  const [workspace, setWorkspace] = useState(null);
  const [recentEvents, setRecentEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [refreshTick, setRefreshTick] = useState(0);
  const [liveEventCount, setLiveEventCount] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      workspaceService.inventory().catch(() => null),
      workspaceService.me().catch(() => null),
      auditService.getLogs(10, 0).catch(() => null),
    ]).then(([invResp, wsResp, evResp]) => {
      if (cancelled) return;
      setInventory(invResp?.data || invResp || null);
      setWorkspace(wsResp?.data || wsResp || null);
      const items = evResp?.data?.items || evResp?.data || evResp?.items || [];
      setRecentEvents(Array.isArray(items) ? items.slice(0, 8) : []);
      setLoading(false);
      setError('');
    }).catch((err) => {
      if (cancelled) return;
      setError(err?.message || 'Failed to load dashboard data');
      setLoading(false);
    });
    return () => { cancelled = true; };
  }, [refreshTick]);

  // Live tick on any SSE event so the operator sees the "Live" badge move.
  useSSE({
    enabled: true,
    onMessage: (evt) => {
      if (!evt?.type) return;
      setLiveEventCount((c) => c + 1);
    },
  });

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
      .filter(([, c]) => c > 0 || tier === 'medium');
  }, [inventory]);

  const shadowActive = !!workspace?.shadow_mode_active;
  const shadowDaysLeft = workspace?.shadow_mode_days_left;

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

      {/* Top metrics row */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <MetricTile
          label="Agents"
          value={loading ? '—' : inventory?.total ?? 0}
          sublabel={`${inventory?.active ?? 0} active · ${inventory?.quarantined ?? 0} quarantined`}
        />
        <MetricTile
          label="High risk"
          value={loading ? '—' : inventory?.high_risk ?? 0}
          sublabel={`${inventory?.by_risk?.critical ?? 0} critical · ${inventory?.by_risk?.high ?? 0} high`}
          accent={(inventory?.high_risk ?? 0) > 0 ? 'text-amber-400' : 'text-white'}
        />
        <MetricTile
          label="Wizard provisioned"
          value={loading ? '—' : inventory?.wizard_provisioned ?? 0}
          sublabel="Created via /onboarding"
        />
        <MetricTile
          label="Shadow mode"
          value={loading ? '—' : shadowActive ? `${shadowDaysLeft ?? '?'}d` : 'OFF'}
          sublabel={shadowActive ? 'Observe-only window' : 'Enforce mode'}
          accent={shadowActive ? 'text-amber-400' : 'text-green-400'}
        />
      </div>

      {/* Inventory hero — provider + risk-tier breakdowns */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card title="By provider" icon={Cpu} className="lg:col-span-2">
          {loading ? (
            <div className="text-xs text-neutral-500 py-6 text-center">Loading…</div>
          ) : providerEntries.length === 0 ? (
            <div className="text-xs text-neutral-500 py-6 text-center space-y-3">
              <div>No agents in this workspace yet.</div>
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
            <div className="text-xs text-neutral-500 py-6 text-center">Loading…</div>
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
          <div className="text-xs text-neutral-500 py-6 text-center">Loading…</div>
        ) : recentEvents.length === 0 ? (
          <div className="text-xs text-neutral-500 py-6 text-center flex flex-col items-center gap-3">
            <CheckCircle2 size={24} className="text-green-400/60" aria-hidden="true" />
            <div>No activity yet — run your agent to see decisions land here.</div>
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
                <span className="text-neutral-300 flex-1 truncate">
                  {e.tool_name || e.reason || e.message || '—'}
                </span>
                <span className="text-[10px] font-mono text-neutral-600 truncate w-16 shrink-0">
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
