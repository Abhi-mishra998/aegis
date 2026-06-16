import React, { useEffect, useState } from 'react';
import {
  AlertOctagon,
  Check,
  CheckCircle2,
  KeyRound,
  PhoneCall,
  RefreshCw,
  ScrollText,
  Slash,
  Webhook,
  X,
} from 'lucide-react';
import { remediationService } from '../../services/api';
import Card from '../Common/Card';
import Button from '../Common/Button';

const POLICY_FLAGS = [
  {
    key: 'revoke_api_keys',
    label: 'Revoke API keys',
    icon: KeyRound,
    blurb: "Marks the agent's `acp_…` keys revoked on a deny.",
  },
  {
    key: 'kill_active_tokens',
    label: 'Kill active tokens',
    icon: Slash,
    blurb: 'Adds outstanding JWTs to the revocation set.',
  },
  {
    key: 'page_oncall',
    label: 'Page on-call',
    icon: PhoneCall,
    blurb: 'Sends an event to the configured pager webhook.',
  },
  {
    key: 'audit_log',
    label: 'Audit log',
    icon: ScrollText,
    blurb: 'Writes a structured audit row with the remediation steps.',
  },
];

function FlagRow({ flag, on }) {
  const Icon = flag.icon;
  return (
    <div className="flex items-start gap-2 text-[11px]">
      <span
        className={
          'mt-0.5 inline-flex items-center justify-center w-4 h-4 rounded-full ' +
          (on ? 'bg-green-500/20 text-green-400' : 'bg-white/[0.04] text-neutral-600')
        }
      >
        {on ? <Check size={10} aria-hidden="true" /> : <X size={10} aria-hidden="true" />}
      </span>
      <Icon size={11} className="mt-1 text-neutral-500 shrink-0" aria-hidden="true" />
      <div className="flex-1 min-w-0">
        <div className={'font-medium ' + (on ? 'text-neutral-200' : 'text-neutral-500')}>
          {flag.label}
        </div>
        <div className="text-[10px] text-neutral-600 leading-snug">{flag.blurb}</div>
      </div>
    </div>
  );
}

function actionRow(action, idx) {
  // Each ledger item carries at least {type, ts/at, status?, target?, detail?}.
  const ts = action.ts || action.at || action.timestamp;
  const tsStr = ts
    ? typeof ts === 'number'
      ? new Date(ts * 1000).toISOString().slice(11, 19)
      : String(ts).slice(11, 19)
    : '—';
  const type = action.type || action.kind || 'action';
  const status = (action.status || 'ok').toLowerCase();
  const dot =
    status === 'ok' || status === 'success'
      ? 'bg-green-500'
      : status === 'failed' || status === 'error'
        ? 'bg-red-500'
        : 'bg-amber-500';
  return (
    <li
      key={action.id || `${type}-${idx}`}
      className="flex items-center gap-3 text-[11px] text-neutral-300 border-b border-white/[0.04] last:border-b-0 py-1.5"
    >
      <span className={`w-1.5 h-1.5 rounded-full ${dot}`} aria-hidden="true" />
      <span className="font-mono text-[10px] text-neutral-500 w-14 shrink-0">{tsStr}</span>
      <span className="font-mono text-[10px] uppercase tracking-wider text-neutral-400 w-28 shrink-0 truncate">
        {type}
      </span>
      <span className="text-[11px] text-neutral-300 flex-1 truncate">
        {action.target || action.detail || action.note || ''}
      </span>
      <span className="text-[10px] text-neutral-500 uppercase tracking-wider">{status}</span>
    </li>
  );
}

/**
 * Sprint 5 — RemediationPanel
 *
 * Reads /remediation/policy + /remediation/incidents/{incident_id}; renders
 * the policy flag set and the chronological ledger. Replay button POSTs to
 * /remediation/incidents/{id}/replay and appends the new actions to the
 * ledger view without a full refetch.
 */
export default function RemediationPanel({ incidentId }) {
  const [policy, setPolicy] = useState(null);
  const [ledger, setLedger] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [replaying, setReplaying] = useState(false);
  const [replayMsg, setReplayMsg] = useState(null);

  const load = () => {
    if (!incidentId) {
      setLoading(false);
      return Promise.resolve();
    }
    setLoading(true);
    setError(null);
    return Promise.all([
      remediationService.getPolicy().catch(() => null),
      remediationService.getLedger(incidentId).catch((err) => {
        const msg = err?.message || '';
        if (/404/.test(msg)) return { data: { items: [] } };
        throw err;
      }),
    ])
      .then(([polResp, ledResp]) => {
        setPolicy(polResp?.data || polResp || null);
        const items = ledResp?.data?.items || ledResp?.items || [];
        setLedger(Array.isArray(items) ? items : []);
        setLoading(false);
      })
      .catch((err) => {
        setError(err?.message || 'Failed to load remediation data');
        setLoading(false);
      });
  };

  useEffect(() => {
    let cancelled = false;
    load().catch(() => {});
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [incidentId]);

  const handleReplay = async () => {
    setReplaying(true);
    setReplayMsg(null);
    try {
      const resp = await remediationService.replay(incidentId);
      const data = resp?.data || resp || {};
      const newActions = data.new_actions || [];
      setLedger((prev) => [...newActions, ...prev]);
      setReplayMsg(
        newActions.length === 0
          ? 'Replay fired — no new actions emitted (policy may be empty).'
          : `Replay fired — ${newActions.length} new action${newActions.length === 1 ? '' : 's'} appended.`,
      );
    } catch (err) {
      setReplayMsg(err?.message || 'Replay failed');
    } finally {
      setReplaying(false);
    }
  };

  return (
    <Card title="Remediation" icon={Webhook}>
      {loading ? (
        <p className="text-[11px] text-neutral-500">Loading…</p>
      ) : error ? (
        <div className="flex items-start gap-2 text-[11px] text-amber-300/80">
          <AlertOctagon size={12} className="mt-0.5 shrink-0" aria-hidden="true" />
          <span>{error}</span>
        </div>
      ) : (
        <div className="space-y-4">
          {/* Policy flags */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {POLICY_FLAGS.map((flag) => (
              <FlagRow key={flag.key} flag={flag} on={!!policy?.[flag.key]} />
            ))}
          </div>
          {policy?.webhook_url && (
            <div className="text-[10px] text-neutral-500 font-mono truncate">
              <Webhook size={10} className="inline mr-1 -mt-0.5" aria-hidden="true" />
              {policy.webhook_url}
            </div>
          )}

          {/* Ledger */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <div className="text-[10px] uppercase tracking-widest text-neutral-500">
                Ledger ({ledger.length})
              </div>
              <Button size="sm" variant="ghost" onClick={handleReplay} disabled={replaying}>
                <RefreshCw
                  size={12}
                  className={replaying ? 'animate-spin' : ''}
                  aria-hidden="true"
                />
                Replay
              </Button>
            </div>
            {ledger.length === 0 ? (
              <div className="flex items-center gap-2 text-[11px] text-neutral-500">
                <CheckCircle2 size={12} className="text-green-400/60" aria-hidden="true" />
                No remediation actions fired for this incident yet.
              </div>
            ) : (
              <ul>{ledger.map(actionRow)}</ul>
            )}
            {replayMsg && (
              <div className="mt-2 text-[10px] text-neutral-400 italic">{replayMsg}</div>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}
