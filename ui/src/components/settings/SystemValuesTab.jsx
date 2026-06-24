import React, { useEffect, useState } from 'react';
import {
  AlertOctagon,
  CheckCircle2,
  DollarSign,
  Plus,
  Save,
  Trash2,
} from 'lucide-react';
import { workspaceService } from '../../services/api';
import { useRole } from '../../hooks/useRole';
import Button from '../Common/Button';

/**
 * Sprint 8 — System Values tab.
 *
 * Lets the workspace OWNER set a dollar weight per resource kind
 * (e.g. table → $50,000). The Blast Radius dollar formula multiplies
 * each untouched-resource kind's count by its weight and surfaces the
 * total as the headline metric on every BlastRadiusCard.
 *
 * UX: a flat key/value form. Pre-seeded with the 6 most common kinds
 * so a first-time owner doesn't stare at an empty form, but each row
 * is deletable and new rows are addable.
 */
const SUGGESTED_KINDS = [
  { kind: 'table',    blurb: 'DB tables (customers, payments, audit_logs)' },
  { kind: 'api',      blurb: 'External APIs / webhooks (stripe, wire-transfer)' },
  { kind: 'secret',   blurb: 'Credentials / API keys' },
  { kind: 'bucket',   blurb: 'Object storage buckets (S3, GCS)' },
  { kind: 'queue',    blurb: 'Message queues / streams (SQS, Kafka)' },
  { kind: 'function', blurb: 'Lambdas / cloud functions' },
];

function formatDollars(value) {
  const n = Number(value) || 0;
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000)     return `$${(n / 1_000).toFixed(0)}K`;
  return `$${n}`;
}

export default function SystemValuesTab() {
  const { canExitShadowMode: isOwner } = useRole(); // OWNER gate reused
  const [rows, setRows] = useState(() =>
    SUGGESTED_KINDS.map((s) => ({ kind: s.kind, blurb: s.blurb, value: '' })),
  );
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [successMsg, setSuccessMsg] = useState('');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    workspaceService
      .me()
      .then((resp) => {
        if (cancelled) return;
        // /workspace/me carries `name`, `tier`, `shadow_mode_until` but not
        // system_values directly. Pull from the /auth/tenants/{id} surface
        // exposed at TenantMetadataCache via the same response.
        const ws = resp?.data || resp || {};
        const sv = ws.system_values || {};
        // Replace zero/null rows in our pre-seeded list, plus append any
        // operator-set kinds that aren't in the suggested list.
        setRows((prev) => {
          const seeded = prev.map((r) => ({
            ...r,
            value: sv[r.kind] != null ? String(sv[r.kind]) : '',
          }));
          const seededKeys = new Set(seeded.map((r) => r.kind));
          const extra = Object.entries(sv)
            .filter(([k]) => !seededKeys.has(k))
            .map(([k, v]) => ({ kind: k, blurb: 'Custom kind', value: String(v) }));
          return [...seeded, ...extra];
        });
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err?.message || 'Failed to load workspace');
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const setRowField = (index, field, value) => {
    setRows((prev) => {
      const next = [...prev];
      next[index] = { ...next[index], [field]: value };
      return next;
    });
    setSuccessMsg('');
  };

  const addRow = () => {
    setRows((prev) => [...prev, { kind: '', blurb: 'Custom kind', value: '' }]);
  };

  const removeRow = (index) => {
    setRows((prev) => prev.filter((_, i) => i !== index));
  };

  const handleSave = async () => {
    setError('');
    setSuccessMsg('');
    const payload = {};
    for (const r of rows) {
      const key = (r.kind || '').trim().toLowerCase();
      if (!key) continue;
      const v = (r.value || '').trim();
      if (v === '' || v === '0') {
        payload[key] = 0; // tells backend to delete the key
        continue;
      }
      const n = Number(v);
      if (Number.isNaN(n) || n < 0) {
        setError(`"${key}" must be a number ≥0 (got "${v}")`);
        return;
      }
      payload[key] = Math.floor(n);
    }

    setSaving(true);
    try {
      const resp = await workspaceService.updateSystemValues(payload);
      const data = resp?.data || resp || {};
      const sv = data.system_values || {};
      const totalKinds = Object.keys(sv).length;
      const totalDollars = Object.values(sv).reduce((s, v) => s + Number(v || 0), 0);
      setSuccessMsg(
        `Saved. ${totalKinds} kinds, ${formatDollars(totalDollars)} total weight.`,
      );
    } catch (err) {
      setError(err?.message || 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <p className="text-[11px] text-neutral-500">Loading workspace…</p>;
  }

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-4 space-y-1.5">
        <div className="text-xs font-bold text-white flex items-center gap-2">
          <DollarSign size={14} aria-hidden="true" /> Blast-Radius dollar weights
        </div>
        <p className="text-[11px] text-neutral-400 leading-snug max-w-2xl">
          Each resource <span className="font-mono text-neutral-300">kind</span> can
          carry a dollar weight. When an incident's blast radius includes 3 untouched
          <span className="font-mono text-neutral-300"> table</span>s × $50,000 + 2
          <span className="font-mono text-neutral-300"> api</span>s × $100,000, the
          BlastRadiusCard surfaces <span className="font-bold text-white">$350,000</span>
          as the "would have hit" number. Set to 0 (or leave blank) to drop a kind from
          the rollup.
        </p>
        {!isOwner && (
          <div className="text-[10px] text-amber-400/80 flex items-center gap-1 pt-1">
            <AlertOctagon size={11} aria-hidden="true" />
            Only the workspace OWNER can save changes — non-owners get a read-only view.
          </div>
        )}
      </div>

      <div className="space-y-2">
        {rows.map((row, idx) => (
          <div
            key={`${row.kind}-${idx}`}
            className="flex items-center gap-3 bg-white/[0.02] border border-white/[0.05] rounded-md px-3 py-2"
          >
            <input name="kind"
              type="text"
              value={row.kind}
              onChange={(e) => setRowField(idx, 'kind', e.target.value)}
              placeholder="kind"
              className="input-standard input-compact w-32 text-xs font-mono"
              disabled={!isOwner}
            />
            <span className="text-[10px] text-neutral-500 flex-1 truncate">{row.blurb}</span>
            <div className="flex items-center gap-1.5">
              <span className="text-[11px] text-neutral-500">$</span>
              <input name="value"
                type="number"
                value={row.value}
                onChange={(e) => setRowField(idx, 'value', e.target.value)}
                placeholder="0"
                min={0}
                className="input-standard input-compact w-28 text-xs text-right"
                disabled={!isOwner}
              />
            </div>
            {isOwner && (
              <button
                type="button"
                onClick={() => removeRow(idx)}
                aria-label={`Remove ${row.kind}`}
                className="text-neutral-600 hover:text-red-400 transition-colors"
              >
                <Trash2 size={13} aria-hidden="true" />
              </button>
            )}
          </div>
        ))}
        {isOwner && (
          <Button size="sm" variant="ghost" onClick={addRow}>
            <Plus size={13} aria-hidden="true" /> Add kind
          </Button>
        )}
      </div>

      {error && (
        <div className="flex items-start gap-2 text-[11px] text-red-400">
          <AlertOctagon size={12} className="mt-0.5 shrink-0" aria-hidden="true" />
          <span>{error}</span>
        </div>
      )}
      {successMsg && (
        <div className="flex items-start gap-2 text-[11px] text-green-400">
          <CheckCircle2 size={12} className="mt-0.5 shrink-0" aria-hidden="true" />
          <span>{successMsg}</span>
        </div>
      )}

      {isOwner && (
        <Button size="sm" onClick={handleSave} disabled={saving} loading={saving}>
          <Save size={13} aria-hidden="true" /> Save weights
        </Button>
      )}
    </div>
  );
}
