import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  ArrowLeft,
  ArrowRight,
  Bot,
  Brain,
  Check,
  CheckCircle2,
  Code2,
  Copy,
  Cpu,
  Database,
  DollarSign,
  FileText,
  Globe,
  Lock,
  Mail,
  Network,
  RefreshCw,
  Server,
  Shield,
  Sparkles,
  Terminal,
  Wand2,
  Workflow,
} from 'lucide-react';
import { registryService } from '../services/api';
import { useSSE } from '../hooks/useSSE';
import Button from '../components/Common/Button';
import Card from '../components/Common/Card';

const PROVIDER_CATALOG = [
  { id: 'anthropic', label: 'Anthropic Claude', icon: Brain, blurb: 'Drop-in for `from anthropic import Anthropic`.', sdk: 'pip install aegis-anthropic==1.1.0' },
  { id: 'openai', label: 'OpenAI', icon: Sparkles, blurb: 'Drop-in for `from openai import OpenAI`.', sdk: 'pip install aegis-openai==1.1.0' },
  { id: 'bedrock', label: 'AWS Bedrock', icon: Cpu, blurb: 'Drop-in for boto3 bedrock-agent-runtime.', sdk: 'pip install aegis-bedrock==1.1.0' },
  { id: 'langchain', label: 'LangChain', icon: Code2, blurb: 'Wraps every Tool so Aegis sees the tool calls.', sdk: 'pip install aegis-langchain==1.1.0' },
  { id: 'cursor', label: 'Cursor', icon: Bot, blurb: 'Cursor MCP server — paste into settings.', sdk: 'npx @aegis/mcp-server' },
  { id: 'claude-code', label: 'Claude Code', icon: Terminal, blurb: 'Claude Code MCP server.', sdk: 'npx @aegis/mcp-server' },
  { id: 'openhands', label: 'OpenHands', icon: Wand2, blurb: 'Routes OpenHands tool calls through Aegis.', sdk: 'aegis-openhands' },
  { id: 'custom', label: 'Custom / HTTP', icon: Globe, blurb: 'Raw HTTP — works from any language.', sdk: 'curl' },
];

// Sprint 13 — Capability-based wizard. The Step-2 question shifted from
// "how risky is this agent?" (the wrong question — CISOs don't know the
// answer in the abstract) to "what can this agent actually do?". Each
// box maps to a canonical Aegis policy set on the backend; the live
// preview panel below the grid shows exactly which rules will fire.
//
// Icons are paired with the founder's vocabulary so the visual scan
// matches what's in the back-of-the-CISO's-mind.
const CAPABILITIES = [
  { id: 'filesystem',     label: 'Filesystem',       blurb: 'Read or write files on disk.',          icon: FileText  },
  { id: 'database',       label: 'Database (SQL)',   blurb: 'Query / mutate tenant databases.',      icon: Database  },
  { id: 'infrastructure', label: 'Infrastructure',   blurb: 'kubectl / terraform / cloud control.',  icon: Server    },
  { id: 'payments',       label: 'Payments',         blurb: 'Wire transfers, refunds, treasury.',    icon: DollarSign },
  { id: 'email',          label: 'Email (outbound)', blurb: 'Send email on behalf of users.',        icon: Mail      },
  { id: 'external_apis',  label: 'External APIs',    blurb: 'HTTP / webhooks to non-tenant hosts.',  icon: Globe     },
  { id: 'internal_apis',  label: 'Internal APIs',    blurb: 'RPC to other tenant-internal services.',icon: Network   },
];

const RISK_LABEL_STYLES = {
  high:   'text-red-400  bg-red-500/10  border-red-500/20',
  medium: 'text-amber-400 bg-amber-500/10 border-amber-500/20',
  low:    'text-green-400 bg-green-500/10 border-green-500/20',
};

function StepHeader({ step, title }) {
  return (
    <div className="flex items-baseline gap-3 mb-2">
      <span className="text-[11px] font-mono uppercase tracking-widest text-neutral-500">
        Step {step} of 3
      </span>
      <h2 className="text-2xl font-bold tracking-tight text-white">{title}</h2>
    </div>
  );
}

function ProviderCard({ provider, selected, onSelect }) {
  const Icon = provider.icon;
  return (
    <button
      type="button"
      onClick={() => onSelect(provider.id)}
      className={
        'group text-left flex gap-3 items-start p-4 rounded-xl border transition-all ' +
        (selected
          ? 'border-white bg-white/[0.06]'
          : 'border-white/[0.07] bg-[#0a0a0a] hover:border-white/20 hover:bg-white/[0.03]')
      }
    >
      <div
        className={
          'w-9 h-9 rounded-lg flex items-center justify-center shrink-0 ' +
          (selected ? 'bg-white text-black' : 'bg-white/[0.05] text-neutral-300 group-hover:text-white')
        }
      >
        <Icon size={18} aria-hidden="true" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-white">{provider.label}</span>
          {selected && <Check size={13} className="text-white shrink-0" aria-hidden="true" />}
        </div>
        <p className="text-xs text-neutral-400 mt-0.5 leading-snug">{provider.blurb}</p>
        <p className="text-[10px] text-neutral-600 mt-1 font-mono truncate">{provider.sdk}</p>
      </div>
    </button>
  );
}

function CopyBlock({ value, label, multiline = false }) {
  const [copied, setCopied] = useState(false);
  const onCopy = () => {
    navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  };
  return (
    <div className="relative group">
      {label && (
        <div className="text-[11px] uppercase tracking-widest text-neutral-500 mb-1">{label}</div>
      )}
      <div
        className={
          'border border-white/[0.07] rounded-xl bg-[#050505] font-mono text-[12px] text-neutral-200 ' +
          (multiline ? 'p-3 whitespace-pre-wrap break-words' : 'px-3 py-2 truncate')
        }
      >
        {value}
      </div>
      <button
        type="button"
        onClick={onCopy}
        aria-label="Copy"
        className="absolute top-2 right-2 p-1.5 rounded-md bg-black/40 border border-white/10 text-neutral-300 hover:bg-black/60 hover:text-white transition-all"
      >
        {copied ? <Check size={13} aria-hidden="true" /> : <Copy size={13} aria-hidden="true" />}
      </button>
    </div>
  );
}

export default function OnboardingWizard() {
  const navigate = useNavigate();

  const [step, setStep] = useState(1);
  const [provider, setProvider] = useState('anthropic');
  const [agentName, setAgentName] = useState('');
  // Sprint 13 — capabilities replace risk_level on Step 2. Default
  // selection is `database` because the SDK-on-endpoint demo (Step 3)
  // walks the customer through a query_database tool call.
  const [capabilities, setCapabilities] = useState(['database']);
  const [policyPreview, setPolicyPreview] = useState(null);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState('');
  const [created, setCreated] = useState(null); // { agent_id, aegis_api_key, ... }
  const [snippet, setSnippet] = useState(null);
  const [snippetError, setSnippetError] = useState('');
  const [firstDecision, setFirstDecision] = useState(null);
  const sseUnsubRef = useRef(null);

  const providerCatalogItem = PROVIDER_CATALOG.find((p) => p.id === provider);

  const canAdvanceFromStep1 = Boolean(provider);
  const canAdvanceFromStep2 = agentName.trim().length >= 3;

  // Sprint 13 — live policy preview. Re-fetch from the wizard backend
  // whenever the capability selection changes so the CISO sees the
  // exact rules being enabled. Debounced via the effect's natural
  // batching — no setInterval, no SSE.
  useEffect(() => {
    let cancelled = false;
    registryService
      .wizardPolicyPreview(capabilities)
      .then((resp) => {
        if (cancelled) return;
        setPolicyPreview(resp?.data || resp || null);
      })
      .catch(() => {
        // Backend offline / 404 — fall back to local stub. UI still renders.
        if (!cancelled) setPolicyPreview(null);
      });
    return () => { cancelled = true; };
  }, [capabilities]);

  const handleStep2Continue = async () => {
    if (!canAdvanceFromStep2 || creating) return;
    setCreating(true);
    setCreateError('');
    try {
      const resp = await registryService.wizard({
        name: agentName.trim(),
        provider,
        capabilities,
      });
      const data = resp?.data || resp;
      if (!data?.aegis_api_key) {
        throw new Error('Wizard returned no Aegis API key');
      }
      setCreated(data);
      try {
        const snip = await registryService.installSnippet(
          data.agent_id,
          provider,
          data.aegis_api_key,
        );
        setSnippet(snip?.data || snip);
      } catch (snipErr) {
        setSnippetError(snipErr.message || 'Snippet fetch failed');
      }
      setStep(3);
    } catch (err) {
      const msg = err.message || 'Failed to create agent';
      setCreateError(
        msg.includes('409') || msg.toLowerCase().includes('already exists')
          ? 'An agent with that name already exists in this workspace. Pick a different name.'
          : msg,
      );
    } finally {
      setCreating(false);
    }
  };

  // SSE — on Step 3, listen for the first /execute decision on this agent_id.
  const sseEnabled = step === 3 && Boolean(created?.agent_id) && !firstDecision;
  const ssePayload = useSSE({
    enabled: sseEnabled,
    onMessage: (evt) => {
      const matchesAgent =
        evt?.agent_id === created?.agent_id ||
        evt?.data?.agent_id === created?.agent_id;
      if (!matchesAgent) return;
      if (
        evt?.type === 'policy_decision' ||
        evt?.type === 'tool_execution' ||
        evt?.type === 'audit_event'
      ) {
        setFirstDecision({
          type: evt.type,
          decision: evt?.data?.decision || evt?.data?.action || 'unknown',
          request_id: evt?.request_id || evt?.data?.request_id || '',
          ts: evt?.ts || Date.now(),
        });
      }
    },
  });

  const stepIndicator = useMemo(
    () => (
      <div className="flex items-center gap-3 mb-6">
        {[1, 2, 3].map((n) => (
          <div key={n} className="flex items-center gap-3">
            <div
              className={
                'w-7 h-7 rounded-full flex items-center justify-center text-[11px] font-semibold transition-all ' +
                (step === n
                  ? 'bg-white text-black'
                  : step > n
                    ? 'bg-green-500/20 text-green-400 border border-green-500/30'
                    : 'bg-white/[0.05] text-neutral-500 border border-white/[0.07]')
              }
            >
              {step > n ? <Check size={13} aria-hidden="true" /> : n}
            </div>
            {n < 3 && (
              <div
                className={'w-12 h-px ' + (step > n ? 'bg-green-500/30' : 'bg-white/[0.07]')}
              />
            )}
          </div>
        ))}
      </div>
    ),
    [step],
  );

  return (
    <div className="min-h-screen bg-[#030303] py-10 px-4">
      <div className="max-w-3xl mx-auto">
        <div className="flex items-center justify-between mb-8">
          <Link
            to="/agents"
            className="inline-flex items-center gap-2 text-xs text-neutral-500 hover:text-neutral-300"
          >
            <ArrowLeft size={14} aria-hidden="true" />
            Back to Agents
          </Link>
          <div className="flex items-center gap-2 text-[11px] text-neutral-600">
            <Shield size={12} aria-hidden="true" />
            <span>Aegis Onboarding Wizard</span>
          </div>
        </div>

        {stepIndicator}

        {step === 1 && (
          <Card>
            <StepHeader step={1} title="Pick your integration" />
            <p className="text-xs text-neutral-400 mb-5 max-w-xl">
              Aegis wraps your agent's tool calls — the LLM call itself stays where it is.
              Pick the SDK that matches your stack; you can change later.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-6">
              {PROVIDER_CATALOG.map((p) => (
                <ProviderCard
                  key={p.id}
                  provider={p}
                  selected={provider === p.id}
                  onSelect={setProvider}
                />
              ))}
            </div>
            <div className="flex justify-end">
              <Button
                onClick={() => setStep(2)}
                disabled={!canAdvanceFromStep1}
                size="sm"
              >
                Continue
                <ArrowRight size={14} aria-hidden="true" />
              </Button>
            </div>
          </Card>
        )}

        {step === 2 && (
          <Card>
            <StepHeader step={2} title="What can this agent do?" />
            <p className="text-xs text-neutral-400 mb-5 max-w-xl">
              Pick the capabilities your agent needs. Aegis auto-enables the
              matching policy set — you don't have to write any Rego. You can
              tighten or loosen these later in <code>Protect → Policies</code>.
            </p>

            <div className="grid grid-cols-1 gap-4 mb-5">
              <div className="space-y-1.5">
                <label className="label-standard" htmlFor="agentName">
                  Agent name <span className="text-red-400">*</span>
                </label>
                <input
                  id="agentName"
                  type="text"
                  className="input-standard h-10"
                  placeholder="finance-bot"
                  value={agentName}
                  onChange={(e) => setAgentName(e.target.value)}
                />
                <p className="text-[10px] text-neutral-600">
                  3-100 chars, lowercase letters + digits + underscore + hyphen.
                </p>
              </div>

              <div className="space-y-2">
                <div className="flex items-baseline justify-between">
                  <label className="label-standard">Capabilities</label>
                  {policyPreview && (
                    <div className="flex items-center gap-2 text-[10px] uppercase tracking-widest">
                      <span className="text-neutral-500">Aggregate risk</span>
                      <span className={`status-badge ${RISK_LABEL_STYLES[policyPreview.risk_level] || RISK_LABEL_STYLES.low}`}>
                        {policyPreview.risk_level} · {policyPreview.risk_score_pct}%
                      </span>
                    </div>
                  )}
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  {CAPABILITIES.map((c) => {
                    const Icon = c.icon;
                    const selected = capabilities.includes(c.id);
                    return (
                      <button
                        key={c.id}
                        type="button"
                        onClick={() => {
                          setCapabilities((prev) =>
                            prev.includes(c.id) ? prev.filter((x) => x !== c.id) : [...prev, c.id],
                          );
                        }}
                        className={
                          'text-left p-3 rounded-xl border transition-all flex gap-3 items-start ' +
                          (selected
                            ? 'border-white bg-white/[0.06]'
                            : 'border-white/[0.07] bg-[#0a0a0a] hover:border-white/20')
                        }
                      >
                        <div className={
                          'w-7 h-7 rounded-md flex items-center justify-center shrink-0 mt-0.5 ' +
                          (selected ? 'bg-white text-black' : 'bg-white/[0.05] text-neutral-300')
                        }>
                          <Icon size={14} aria-hidden="true" />
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-semibold text-white">{c.label}</span>
                            {selected && <Check size={13} className="text-white shrink-0" aria-hidden="true" />}
                          </div>
                          <div className="text-[11px] text-neutral-500 mt-0.5 leading-snug">{c.blurb}</div>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* Live preview of the policies Aegis will turn on */}
              {policyPreview && policyPreview.policies_enabled?.length > 0 && (
                <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-3 space-y-2">
                  <div className="flex items-center gap-2 text-[10px] uppercase tracking-widest text-neutral-500">
                    <Workflow size={11} aria-hidden="true" />
                    <span>Policies Aegis will enable for this agent</span>
                    <span className="ml-auto text-neutral-400">{policyPreview.policies_enabled.length}</span>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {policyPreview.policies_enabled.map((p) => (
                      <span
                        key={p}
                        className="inline-flex items-center gap-1 text-[10px] text-neutral-200 px-2 py-0.5 rounded-md bg-white/[0.04] border border-white/[0.06] font-mono"
                      >
                        <Shield size={9} className="text-neutral-500" />
                        {p}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {policyPreview && policyPreview.policies_enabled?.length === 0 && (
                <div className="rounded-xl border border-neutral-700 bg-neutral-900/40 p-3 text-[11px] text-neutral-500">
                  No capabilities selected — Aegis will still log every tool call,
                  but no runtime gate will fire. Pick at least one capability above
                  to enable a policy set.
                </div>
              )}

              <div className="rounded-xl border border-amber-500/20 bg-amber-500/[0.04] p-3 flex gap-3">
                <Lock size={14} className="text-amber-400 mt-0.5 shrink-0" aria-hidden="true" />
                <div className="text-xs leading-snug text-amber-200/90">
                  <strong className="text-amber-100">Your LLM key stays on your machine.</strong>{' '}
                  Aegis never asks for your Anthropic / OpenAI / Bedrock key. The only
                  Aegis-issued credential is the <code>acp_…</code> key, shown on the next step.
                </div>
              </div>

              {createError && (
                <div className="text-xs text-red-400 bg-red-500/[0.06] border border-red-500/20 rounded-xl p-3">
                  {createError}
                </div>
              )}
            </div>

            <div className="flex justify-between">
              <Button variant="ghost" onClick={() => setStep(1)} size="sm">
                <ArrowLeft size={14} aria-hidden="true" />
                Back
              </Button>
              <Button
                onClick={handleStep2Continue}
                disabled={!canAdvanceFromStep2 || creating}
                loading={creating}
                size="sm"
              >
                Generate Aegis key
                <ArrowRight size={14} aria-hidden="true" />
              </Button>
            </div>
          </Card>
        )}

        {step === 3 && created && (
          <Card>
            <StepHeader step={3} title="Install + connect" />
            <p className="text-xs text-neutral-400 mb-5 max-w-xl">
              Copy the snippet into your project, set your env vars, run your agent.
              We'll flip the panel below when the first tool call lands.
            </p>

            <div className="space-y-4 mb-5">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <CopyBlock label="Agent ID" value={created.agent_id} />
                <CopyBlock label="Aegis API key (copy now — shown once)" value={created.aegis_api_key} />
              </div>

              {snippet ? (
                <>
                  <CopyBlock label="Install" value={snippet.install_command} />
                  <CopyBlock label="Snippet" value={snippet.snippet} multiline />
                  {snippet.notes?.length > 0 && (
                    <ul className="text-[11px] text-neutral-500 space-y-1">
                      {snippet.notes.map((n) => (
                        <li key={n} className="flex gap-2 items-start">
                          <span className="text-neutral-700">•</span>
                          <span>{n}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </>
              ) : snippetError ? (
                <div className="text-xs text-red-400">{snippetError}</div>
              ) : (
                <div className="text-xs text-neutral-500">Loading snippet…</div>
              )}

              <div
                className={
                  'rounded-xl border p-4 flex items-center gap-3 transition-all ' +
                  (firstDecision
                    ? 'border-green-500/30 bg-green-500/[0.05]'
                    : 'border-white/[0.07] bg-[#050505]')
                }
                aria-live="polite"
              >
                {firstDecision ? (
                  <>
                    <CheckCircle2 size={18} className="text-green-400 shrink-0" aria-hidden="true" />
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-semibold text-white">
                        First decision received
                      </div>
                      <div className="text-[11px] text-neutral-400 mt-0.5">
                        {firstDecision.type} · {firstDecision.decision}
                        {firstDecision.request_id && (
                          <> · <code className="font-mono">{firstDecision.request_id.slice(0, 8)}…</code></>
                        )}
                      </div>
                    </div>
                    <Button
                      size="sm"
                      onClick={() => navigate(`/agents/${created.agent_id}/profile`)}
                    >
                      View agent
                    </Button>
                  </>
                ) : (
                  <>
                    <RefreshCw size={16} className="text-neutral-500 animate-spin shrink-0" aria-hidden="true" />
                    <div className="flex-1 text-sm text-neutral-300">
                      Waiting for your agent's first tool call…
                    </div>
                    <div className="text-[10px] text-neutral-600">
                      SSE {ssePayload?.state || 'connecting'}
                    </div>
                  </>
                )}
              </div>
            </div>

            <div className="flex justify-between">
              <Link to="/agents" className="text-xs text-neutral-500 hover:text-neutral-300 inline-flex items-center gap-1">
                <ArrowLeft size={12} aria-hidden="true" />
                Done — back to Agents
              </Link>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => navigate(`/agents/${created.agent_id}/profile`)}
              >
                Agent profile
                <ArrowRight size={14} aria-hidden="true" />
              </Button>
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}
