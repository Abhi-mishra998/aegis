import React, { useEffect, useState } from 'react';
import { AlertOctagon, Shield } from 'lucide-react';
import { iagService } from '../../services/api';
import Card from '../Common/Card';

const SEVERITY_TIER = {
  CRITICAL: { bg: 'bg-red-500/30',    text: 'text-red-200',    border: 'border-red-500/50',    label: 'Critical' },
  HIGH:     { bg: 'bg-orange-500/30', text: 'text-orange-200', border: 'border-orange-500/50', label: 'High'     },
  MEDIUM:   { bg: 'bg-amber-500/25',  text: 'text-amber-200',  border: 'border-amber-500/40',  label: 'Medium'   },
  LOW:      { bg: 'bg-green-500/20',  text: 'text-green-200',  border: 'border-green-500/30',  label: 'Low'      },
};

function tierFor(severity) {
  const key = String(severity || '').toUpperCase();
  return SEVERITY_TIER[key] || SEVERITY_TIER.LOW;
}

// `scoped` = an agent_id was passed → matrix is in per-agent mode and
// untouched cells should look dimmed/dashed. Without an agent the
// matrix is informational (everything looks at full saturation).
function TechniqueCell({ technique, scoped }) {
  const tier = tierFor(technique.max_severity);
  const sigCount = technique.signals?.length || 0;
  const touched = !!technique.touched;
  // Dim + dash untouched cells when scoped to an agent so operators
  // see at a glance which techniques the agent actually used vs the
  // unused-but-reachable matrix surface.
  const cellClasses = scoped && !touched
    ? `border-dashed ${tier.border} ${tier.bg} opacity-40`
    : `${tier.border} ${tier.bg}`;
  const titleLines = (technique.signals || []).map((s) => {
    const tag = scoped ? (s.touched ? "● fired" : "○ not fired") : "";
    return `${s.id} (${s.severity}, score=${s.default_score})${tag ? ` ${tag}` : ""}: ${s.description}`;
  });
  return (
    <div
      className={
        `border rounded-md px-2 py-1.5 space-y-0.5 cursor-help group relative ${cellClasses}`
      }
      title={titleLines.join('\n\n')}
    >
      <div className="flex items-center gap-1">
        <div className={`text-[10px] font-mono ${tier.text}`}>{technique.technique_id}</div>
        {scoped && touched && (
          <span
            className="text-[8px] font-bold uppercase tracking-widest text-emerald-300/80"
            aria-label="fired in window"
          >
            • fired
          </span>
        )}
      </div>
      <div className="text-[10px] text-neutral-300 leading-tight truncate">
        {technique.technique_name}
      </div>
      <div className="text-[9px] text-neutral-500 uppercase tracking-widest">
        {scoped
          ? `${technique.touched_count || 0}/${sigCount} fired · ${tier.label}`
          : `${sigCount} sig${sigCount === 1 ? '' : 's'} · ${tier.label}`}
      </div>
    </div>
  );
}

function TacticColumn({ tactic, scoped }) {
  const touched = !!tactic.touched;
  return (
    <div className={`space-y-2 min-w-[140px] ${scoped && !touched ? 'opacity-70' : ''}`}>
      <div className="space-y-0.5 sticky top-0 bg-[#040404] pb-2 border-b border-white/[0.06]">
        <div className="flex items-center gap-1">
          <div className="text-[10px] font-mono text-neutral-500">{tactic.tactic_id}</div>
          {scoped && touched && (
            <span className="ml-auto text-[9px] font-bold text-emerald-400">
              {tactic.touched_techniques}/{tactic.technique_count}
            </span>
          )}
        </div>
        <div className="text-xs font-semibold text-white truncate">{tactic.tactic_name}</div>
        <div className="text-[9px] uppercase tracking-widest text-neutral-600">
          {scoped
            ? `${tactic.touched_signals || 0}/${tactic.signal_count} fired`
            : `${tactic.signal_count} sig · ${tactic.technique_count} tech`}
        </div>
      </div>
      <div className="space-y-1.5">
        {tactic.techniques.map((t) => (
          <TechniqueCell key={t.technique_id} technique={t} scoped={scoped} />
        ))}
      </div>
    </div>
  );
}

/**
 * Sprint 7 — MITRE ATT&CK coverage grid.
 *
 * Pulls /iag/mitre-coverage; renders one column per tactic, one cell per
 * technique. Cell colour = max severity within that technique's signals.
 * Hovering a cell surfaces the signal_id + score + description tooltip.
 *
 * Used standalone on /threat-graph and embedded as the right panel of
 * the ThreatGraph page.
 */
export default function MitreCoverageGrid({ compact = false, agentId = null, days = 7 }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    iagService
      .getMitreCoverage(agentId, days)
      .then((resp) => {
        if (cancelled) return;
        const payload = resp?.data || resp || null;
        setData(payload);
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err?.message || 'Failed to load MITRE coverage');
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [agentId, days]);

  const scoped = !!agentId;

  return (
    <Card title="MITRE ATT&CK coverage" icon={Shield}>
      {loading ? (
        <p className="text-[11px] text-neutral-500">Loading…</p>
      ) : error ? (
        <div className="flex items-start gap-2 text-[11px] text-amber-300/80">
          <AlertOctagon size={12} className="mt-0.5 shrink-0" aria-hidden="true" />
          <span>{error}</span>
        </div>
      ) : data ? (
        <div className="space-y-3">
          <div className="flex flex-wrap items-baseline gap-3 text-[11px] text-neutral-400">
            {scoped ? (
              <div>
                <span className="font-bold text-emerald-400">{data.touched_tactics}</span>
                {' / '}
                <span className="font-bold text-white">{data.tactic_total}</span> tactics fired
                {' · '}
                <span className="font-bold text-emerald-400">{data.touched_techniques_total}</span>
                {' / '}
                <span className="font-bold text-white">
                  {data.tactics.reduce((acc, t) => acc + t.technique_count, 0)}
                </span> techniques
                <span className="text-neutral-500"> · last {data.days}d</span>
              </div>
            ) : (
              <div>
                <span className="font-bold text-white">{data.signal_total}</span> signals
                across <span className="font-bold text-white">{data.tactic_total}</span> tactics
              </div>
            )}
            <div className="flex items-center gap-3 ml-auto flex-wrap">
              {scoped && (
                <span className="flex items-center gap-1 text-[10px] text-neutral-400">
                  <span className="inline-block w-2.5 h-2.5 rounded bg-emerald-500/30 border border-emerald-500/50 border-solid" />
                  Touched
                </span>
              )}
              {scoped && (
                <span className="flex items-center gap-1 text-[10px] text-neutral-500">
                  <span className="inline-block w-2.5 h-2.5 rounded border border-neutral-500/40 border-dashed opacity-40" />
                  Reachable / not fired
                </span>
              )}
              {(['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']).map((sev) => {
                const tier = SEVERITY_TIER[sev];
                return (
                  <span key={sev} className="flex items-center gap-1 text-[10px]">
                    <span className={`inline-block w-2.5 h-2.5 rounded ${tier.bg} ${tier.border} border`} />
                    {tier.label}
                  </span>
                );
              })}
            </div>
          </div>
          <div
            className={
              compact
                ? 'overflow-x-auto pb-2 max-h-[420px] overflow-y-auto'
                : 'overflow-x-auto pb-2'
            }
          >
            <div className="flex gap-3 items-start min-w-max">
              {data.tactics.map((tactic) => (
                <TacticColumn key={tactic.tactic_id} tactic={tactic} scoped={scoped} />
              ))}
            </div>
          </div>
        </div>
      ) : (
        <p className="text-[11px] text-neutral-500 italic">No coverage data.</p>
      )}
    </Card>
  );
}
