import React, { useEffect, useState } from 'react';
import {
  AlertOctagon,
  Layers,
  Network,
  Shield,
  Target,
} from 'lucide-react';
import { iagService } from '../../services/api';
import Card from '../Common/Card';

function formatTs(epoch) {
  if (!epoch || epoch <= 0) return 'never';
  try {
    return new Date(epoch * 1000).toISOString().slice(0, 16).replace('T', ' ');
  } catch {
    return '—';
  }
}

function CriticalityPill({ score }) {
  const n = Number(score) || 0;
  const tier =
    n >= 50 ? { label: 'Critical', cls: 'bg-red-500/15 text-red-400 border-red-500/30' }
    : n >= 25 ? { label: 'High',     cls: 'bg-orange-500/15 text-orange-400 border-orange-500/30' }
    : n >= 10 ? { label: 'Medium',   cls: 'bg-amber-500/15 text-amber-400 border-amber-500/30' }
    :          { label: 'Low',      cls: 'bg-green-500/15 text-green-400 border-green-500/30' };
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider border ${tier.cls}`}>
      {tier.label} · {n}
    </span>
  );
}

function formatDollars(value) {
  const n = Number(value) || 0;
  if (n >= 1_000_000_000) return `$${(n / 1_000_000_000).toFixed(1)}B`;
  if (n >= 1_000_000)     return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000)         return `$${(n / 1_000).toFixed(0)}K`;
  return `$${n}`;
}

/** Sprint 8 — Blast-radius dollar headline. */
function DollarHeadline({ dollarEstimate, systemValuesConfigured, byKindDollars }) {
  if (!systemValuesConfigured) {
    return (
      <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2 text-[10px] text-neutral-500 leading-snug">
        Set per-resource-kind dollar weights in{' '}
        <a href="/settings?tab=system-values" className="text-white hover:underline">
          Settings → System Values
        </a>{' '}
        to see a $ blast-radius headline here.
      </div>
    );
  }
  return (
    <div className="rounded-lg border border-red-500/20 bg-red-500/[0.06] p-3 space-y-1">
      <div className="text-[10px] uppercase tracking-widest text-red-400/80">
        Could have reached
      </div>
      <div className="text-2xl font-bold text-white">
        {formatDollars(dollarEstimate)}
      </div>
      {byKindDollars && Object.keys(byKindDollars).length > 0 && (
        <div className="flex flex-wrap gap-1.5 pt-1">
          {Object.entries(byKindDollars).map(([kind, dollars]) => (
            <span
              key={kind}
              className="inline-flex items-center gap-1 text-[10px] font-mono text-red-200 bg-red-500/[0.08] border border-red-500/20 rounded px-1.5 py-0.5"
            >
              {kind}: {formatDollars(dollars)}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function ResourceList({ items, emptyLabel, limit = 8 }) {
  if (!items || items.length === 0) {
    return <p className="text-[11px] text-neutral-600 italic">{emptyLabel}</p>;
  }
  const shown = items.slice(0, limit);
  const more = items.length - shown.length;
  return (
    <ul className="space-y-1">
      {shown.map((r) => (
        <li
          key={r}
          className="font-mono text-[11px] text-neutral-300 bg-white/[0.03] border border-white/[0.04] rounded px-2 py-1 truncate"
          title={r}
        >
          {r}
        </li>
      ))}
      {more > 0 && (
        <li className="text-[10px] text-neutral-500 italic">…and {more} more</li>
      )}
    </ul>
  );
}

/**
 * Sprint 5 — BlastRadiusCard
 *
 * Pulls /iag/incidents/{incident_id}/blast-radius and renders the
 * touched-vs-untouched split + criticality score + by-kind breakdown.
 * Tolerates 404 (no IAG data ingested yet) and 409 (incident has no
 * agents) with graceful fallbacks — these are not bugs, just states.
 */
export default function BlastRadiusCard({ incidentId }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!incidentId) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    iagService
      .getBlastRadius(incidentId)
      .then((resp) => {
        if (cancelled) return;
        setData(resp?.data || resp || null);
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        const msg = err?.message || '';
        if (/404/.test(msg)) {
          setError('No IAG data for this incident yet.');
        } else if (/409/.test(msg)) {
          setError('Incident has no participating agents — IAG cannot compute.');
        } else {
          setError(msg || 'Failed to load blast radius');
        }
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [incidentId]);

  return (
    <Card title="Blast Radius" icon={Target}>
      {loading ? (
        <p className="text-[11px] text-neutral-500">Loading…</p>
      ) : error ? (
        <div className="flex items-start gap-2 text-[11px] text-amber-300/80">
          <AlertOctagon size={12} className="mt-0.5 shrink-0" aria-hidden="true" />
          <span>{error}</span>
        </div>
      ) : data ? (
        <div className="space-y-3">
          {/* Sprint 8 — Dollar headline (only renders when system_values is configured). */}
          {(data.dollar_estimate > 0 || data.system_values_configured) && (
            <DollarHeadline
              dollarEstimate={data.dollar_estimate || 0}
              systemValuesConfigured={!!data.system_values_configured}
              byKindDollars={data.by_kind_dollars || {}}
            />
          )}
          <div className="flex items-center gap-3 flex-wrap">
            <CriticalityPill score={data.criticality_score} />
            <div className="text-[10px] text-neutral-500">
              <Network size={10} className="inline mr-1 -mt-0.5" aria-hidden="true" />
              Last ingest: {formatTs(data.last_ingest_ts)}
            </div>
            {data.participating_agents?.length > 0 && (
              <div className="text-[10px] text-neutral-500">
                {data.participating_agents.length} agent{data.participating_agents.length > 1 ? 's' : ''}
              </div>
            )}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <div>
              <div className="text-[10px] uppercase tracking-widest text-red-400/80 mb-1.5">
                Touched ({data.touched_resources?.length || 0})
              </div>
              <ResourceList
                items={data.touched_resources}
                emptyLabel="No resources confirmed touched."
              />
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-widest text-amber-400/80 mb-1.5">
                Reachable but untouched ({data.untouched_resources?.length || 0})
              </div>
              <ResourceList
                items={data.untouched_resources}
                emptyLabel="No accessible resources beyond what was touched."
              />
            </div>
          </div>

          {data.by_kind && Object.keys(data.by_kind).length > 0 && (
            <div>
              <div className="text-[10px] uppercase tracking-widest text-neutral-500 mb-1.5 flex items-center gap-1">
                <Layers size={10} aria-hidden="true" />
                By kind
              </div>
              <div className="flex flex-wrap gap-2">
                {Object.entries(data.by_kind).map(([kind, count]) => (
                  <span
                    key={kind}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-white/[0.04] border border-white/[0.06] text-[10px] text-neutral-300 font-mono"
                  >
                    <Shield size={9} aria-hidden="true" />
                    {kind} · {count}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      ) : (
        <p className="text-[11px] text-neutral-500 italic">No data.</p>
      )}
    </Card>
  );
}
