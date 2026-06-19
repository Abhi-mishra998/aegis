// Sprint S3 (2026-06-19) — Per-vendor SIEM card with Test-Connection button.
//
// One card per supported vendor (Splunk / Datadog / Elastic / Sentinel /
// Chronicle). The vendor metadata + field schema is fetched once from
// GET /siem/vendors so adding a new vendor server-side automatically
// surfaces it in the UI without an additional commit here.

import React, { useState } from 'react';
import { Loader2, Play, AlertCircle, ChevronRight, Check } from 'lucide-react';
import { IntegrationCard, SecretInput, StatusBadge } from './Common/ConnectorPrimitives';
import { siemService } from '../services/api';

export function SiemVendorCard({ vendor, savedConfig, onSave }) {
  const [open, setOpen] = useState(false);
  const [creds, setCreds] = useState(() => initialCreds(vendor, savedConfig));
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState(null);
  const [saving, setSaving] = useState(false);

  const handleTest = async () => {
    setTesting(true);
    setResult(null);
    try {
      const resp = await siemService.test({
        vendor: vendor.id,
        credentials: creds,
      });
      setResult(resp?.data || resp);
    } catch (err) {
      setResult({ status: 'error', detail: err.message || 'Network error' });
    } finally {
      setTesting(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave(vendor.id, creds);
    } finally {
      setSaving(false);
    }
  };

  const isConnected = Boolean(savedConfig && Object.keys(savedConfig).length > 0);

  return (
    <IntegrationCard
      title={vendor.label}
      description={vendor.doc_hint}
      icon={null}
    >
      <div className="space-y-3">
        <div className="flex items-center gap-3">
          {isConnected ? (
            <span className="flex items-center gap-1.5 text-xs text-emerald-400">
              <Check size={12} className="bg-emerald-500/20 rounded-full p-0.5" />
              Saved
            </span>
          ) : (
            <span className="text-xs text-neutral-500">Not connected</span>
          )}
          <button
            onClick={() => setOpen((v) => !v)}
            className="ml-auto text-xs text-neutral-400 hover:text-neutral-200 flex items-center gap-1"
          >
            {open ? 'Hide' : 'Configure'} <ChevronRight size={11} className={open ? 'rotate-90 transition-transform' : 'transition-transform'} />
          </button>
        </div>

        {open && (
          <div className="space-y-3 pt-2 border-t border-[var(--border-subtle)]">
            {vendor.fields.map((field) => (
              <FieldRow
                key={field.name}
                field={field}
                value={creds[field.name] ?? field.default ?? ''}
                onChange={(v) => setCreds((c) => ({ ...c, [field.name]: v }))}
              />
            ))}

            <div className="flex items-center gap-3 pt-1">
              <button
                onClick={handleTest}
                disabled={testing}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border-subtle)] text-xs text-neutral-300 hover:border-white/20 disabled:opacity-40"
              >
                {testing ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
                Test Connection
              </button>
              {result && <StatusBadge result={result} />}
              {result?.latency_ms !== undefined && (
                <span className="text-xs text-neutral-500">{result.latency_ms} ms</span>
              )}
              <button
                onClick={handleSave}
                disabled={saving || (result?.status !== 'ok' && !isConnected)}
                title={
                  result?.status === 'ok' || isConnected
                    ? 'Persist credentials'
                    : 'Run Test Connection first; Save unlocks on green'
                }
                className="ml-auto px-3 py-1.5 rounded-lg bg-emerald-600/20 border border-emerald-600/40 text-xs text-emerald-200 hover:bg-emerald-600/30 disabled:opacity-40"
              >
                {saving ? 'Saving…' : 'Save & Activate'}
              </button>
            </div>

            {result?.status === 'error' && (
              <p className="flex items-start gap-1 text-[11px] text-red-400">
                <AlertCircle size={11} className="mt-0.5 shrink-0" /> {result.detail}
              </p>
            )}
            {vendor.doc_hint && (
              <p className="text-[11px] text-neutral-600 leading-relaxed">
                <span className="text-neutral-500">Where to find:</span> {vendor.doc_hint}
              </p>
            )}
          </div>
        )}
      </div>
    </IntegrationCard>
  );
}

function FieldRow({ field, value, onChange }) {
  if (field.type === 'select') {
    return (
      <div>
        <label className="block text-xs text-neutral-400 mb-1">{field.label}</label>
        <select
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="w-full bg-neutral-900 border border-[var(--border-subtle)] rounded-md px-2 py-1.5 text-xs text-neutral-200"
        >
          {(field.options || []).map((opt) => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
      </div>
    );
  }
  if (field.type === 'secret') {
    return (
      <SecretInput
        id={field.name}
        label={field.label}
        placeholder={field.placeholder}
        value={value}
        onChange={onChange}
      />
    );
  }
  return (
    <div>
      <label className="block text-xs text-neutral-400 mb-1">{field.label}</label>
      <input
        type={field.type === 'url' ? 'url' : 'text'}
        placeholder={field.placeholder || ''}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full bg-neutral-900 border border-[var(--border-subtle)] rounded-md px-2 py-1.5 text-xs text-neutral-200 placeholder:text-neutral-600"
      />
    </div>
  );
}

function initialCreds(vendor, savedConfig) {
  const out = {};
  for (const field of vendor.fields) {
    out[field.name] = savedConfig?.[field.name] ?? field.default ?? '';
  }
  return out;
}
