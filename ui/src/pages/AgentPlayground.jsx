import React, { useState, useEffect, useRef, useMemo } from 'react'
import { Link } from 'react-router-dom'
import {
  Terminal, Play, AlertTriangle, Clock, ChevronDown, ChevronRight,
  Copy, Check, Shield, Zap, Wand2, Plus, Bot,
} from 'lucide-react'
import Card from '../components/Common/Card'
import SkeletonLoader from '../components/Common/SkeletonLoader'
import { playgroundService, registryService } from '../services/api'
import { useAgents } from '../hooks/useAgents'

const DECISION_META = {
  allow:    { label: 'ALLOW',    bg: 'bg-green-500/10',  text: 'text-green-400',  border: 'border-green-500/20'  },
  monitor:  { label: 'MONITOR',  bg: 'bg-yellow-500/10', text: 'text-yellow-400', border: 'border-yellow-500/20' },
  throttle: { label: 'THROTTLE', bg: 'bg-orange-500/10', text: 'text-orange-400', border: 'border-orange-500/20' },
  escalate: { label: 'ESCALATE', bg: 'bg-purple-500/10', text: 'text-purple-400', border: 'border-purple-500/20' },
  deny:     { label: 'DENY',     bg: 'bg-red-500/10',    text: 'text-red-400',    border: 'border-red-500/20'    },
  block:    { label: 'BLOCK',    bg: 'bg-red-500/10',    text: 'text-red-400',    border: 'border-red-500/20'    },
  kill:     { label: 'KILL',     bg: 'bg-red-900/20',    text: 'text-red-300',    border: 'border-red-700/30'    },
}

const SIGNAL_COLORS = {
  inference:   '#ef4444',
  behavior:    '#f97316',
  anomaly:     '#eab308',
  cost:        '#3b82f6',
  cross_agent: '#8b5cf6',
}

// Per-tool sample payloads — when a client picks a tool, this seeds a
// realistic body so they don't have to remember field names. Covers the
// 4 demo agents' allow-listed tools + canonical hostile payloads under
// "Test scenarios" so attack simulation is one click away.
const TOOL_PAYLOADS = {
  // demo-agent tools
  search_web:    { query: 'AI governance best practices' },
  send_email:    { to: 'customer@example.com', subject: 'Order update', body: 'Your order has been shipped.' },
  run_code:      { language: 'python', exec: 'print("hello world")' },
  ping:          {},
  read_file:     { path: '/var/log/app.log' },
  // db-copilot-demo tools
  run_query:     { query: 'SELECT count(*) FROM orders WHERE status = 1' },
  'db.query':    { sql: 'SELECT 1' },
  'db.execute':  { sql: 'UPDATE orders SET status = 2 WHERE id = 1' },
  // support-agent-demo tools
  'email.send':                 { to: 'customer@example.com', body: 'Hi — your ticket is being investigated.' },
  'slack.send':                 { channel: '#support', message: 'New ticket #1234' },
  'crm.get_customer':           { customer_id: 'C-001' },
  'crm.list_customers':         { limit: 20 },
  'crm.lookup_ticket':          { ticket_id: 'T-001' },
  'crm.update_ticket':          { ticket_id: 'T-001', status: 'resolved' },
  'crm.get_billing':            { customer_id: 'C-001' },
  'crm.bulk_export':            { format: 'csv', limit: 100 },
  // devops-agent-demo tools
  kubectl_get:                  { resource: 'pods', namespace: 'default' },
  kubectl_delete:               { resource: 'pod', name: 'broken-pod-1', namespace: 'staging' },
  'k8s.get.pod':                { name: 'web-1', namespace: 'default' },
  'k8s.get.deployment':         { name: 'web', namespace: 'default' },
  'k8s.get.namespace':          { name: 'default' },
  'k8s.get.node':               { name: 'worker-1' },
  'k8s.list.pods':              { namespace: 'default' },
  'k8s.list.deployments':       { namespace: 'default' },
  'k8s.list.namespaces':        {},
  'k8s.list.secrets':           { namespace: 'default' },
  'k8s.delete.pod':             { name: 'broken-pod-1', namespace: 'staging' },
  'k8s.delete.namespace':       { name: 'staging' },
  'k8s.delete.node':            { name: 'old-worker-1' },
  'k8s.apply.configmap':        { name: 'app-config', namespace: 'staging', data: { LOG_LEVEL: 'debug' } },
  'k8s.exec.pod':               { name: 'web-1', namespace: 'default', command: 'ls /' },
  'k8s.logs.pod':               { name: 'web-1', namespace: 'default', tail: 200 },
  'k8s.scale.deployment':       { name: 'web', namespace: 'default', replicas: 3 },
  'k8s.describe.pod':           { name: 'web-1', namespace: 'default' },
  'k8s.describe.deployment':    { name: 'web', namespace: 'default' },
  'k8s.top.nodes':              {},
  'k8s.get.secret':             { name: 'db-creds', namespace: 'default' },
  'k8s.get.clusterrole':        { name: 'view' },
  'k8s.list.clusterroles':      {},
  'k8s.create.clusterrolebinding': { name: 'devops-readonly', role: 'view' },
  execute_agent:                {},
  execute_command:              { cmd: 'kubectl get pods' },
  execute_shell:                { cmd: 'date' },
  list_dir:                     { path: '/tmp' },
  write_file:                   { path: '/tmp/note.txt', content: 'demo' },
  delete_file:                  { path: '/tmp/note.txt' },
  network_request:              { method: 'GET', url: 'https://api.example.com/health' },
  query_database:               { sql: 'SELECT 1' },
  call_api:                     { method: 'GET', url: 'https://api.example.com/v1/orders' },
  list_files:                   { path: '/' },
  read_config:                  { key: 'app_name', namespace: 'public' },
}

// Attack scenarios — one click loads a hostile payload that the gateway
// should hard-deny. Lets clients verify the full kill chain end-to-end.
const ATTACK_SCENARIOS = [
  {
    id:    'pii_ssn',
    label: 'PII exfiltration (SSN)',
    tool:  'send_email',
    payload: { body: 'Customer SSN is 123-45-6789, DOB 01/01/1985' },
    expect: 'block',
  },
  {
    id:    'rce_rm',
    label: 'RCE (rm -rf /)',
    tool:  'run_code',
    payload: { exec: 'os.system("rm -rf /")' },
    expect: 'block',
  },
  {
    id:    'sqli_drop',
    label: 'SQL injection (DROP TABLE)',
    tool:  'run_query',
    payload: { query: "SELECT * FROM users WHERE 1=1; DROP TABLE users; --" },
    expect: 'block',
  },
  {
    id:    'k8s_prod',
    label: 'Destructive k8s on production',
    tool:  'kubectl_delete',
    payload: { resource: 'all', namespace: 'production' },
    expect: 'block',
  },
]

const fallbackPayload = (toolName) => {
  // Last-resort empty params — gives the user a starting point even for
  // tools we don't have a curated sample for.
  if (TOOL_PAYLOADS[toolName]) return TOOL_PAYLOADS[toolName]
  if (toolName?.endsWith('.list') || toolName?.startsWith('list_')) return {}
  return { input: 'replace-me' }
}

const formatJson = (obj) => JSON.stringify(obj, null, 2)

function SignalBar({ label, value, color }) {
  const pct = Math.round((value || 0) * 100)
  return (
    <div className="flex items-center gap-3">
      <span className="text-xs text-neutral-500 w-24 capitalize">{label.replace('_', ' ')}</span>
      <div className="flex-1 h-1.5 bg-white/[0.05] rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${pct}%`, background: color }}
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
        />
      </div>
      <span className="text-xs font-mono text-neutral-400 w-10 text-right">{(value || 0).toFixed(2)}</span>
    </div>
  )
}

function CopyButton({ text }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      type="button"
      onClick={() => navigator.clipboard.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000) })}
      aria-label="Copy to clipboard"
      className="p-1.5 rounded text-neutral-500 hover:text-white hover:bg-white/[0.05] transition-colors"
    >
      {copied ? <Check size={12} className="text-green-400" aria-hidden="true" /> : <Copy size={12} aria-hidden="true" />}
    </button>
  )
}

export default function AgentPlayground() {
  const { agents, selectedAgentId, setSelectedAgentId, agentsLoading } = useAgents()
  const mounted = useRef(true)

  // Local alias — playground can override selection without affecting global context
  const [localAgentId,     setLocalAgentId]     = useState('')
  const [tool,             setTool]             = useState('')
  const [payload,          setPayload]          = useState('{}')
  const [payloadError,     setPayloadError]     = useState(null)
  const [executing,        setExecuting]        = useState(false)
  const [result,           setResult]           = useState(null)
  const [execError,        setExecError]        = useState(null)
  const [rawOpen,          setRawOpen]          = useState(false)
  const [history,          setHistory]          = useState([])
  const [allowedTools,     setAllowedTools]     = useState([])
  const [toolsLoading,     setToolsLoading]     = useState(false)
  // Tracks the agent whose tools we last loaded — guards against stale
  // races when the user flips agents while a permission fetch is in flight.
  const lastLoadedAgentRef = useRef(null)

  const selectedAgent = localAgentId

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  // Sync local selection with global context on first render
  useEffect(() => {
    if (selectedAgentId && !localAgentId) {
      setLocalAgentId(selectedAgentId)
    }
  }, [selectedAgentId]) // eslint-disable-line react-hooks/exhaustive-deps

  // Whenever the agent changes: (1) load its allow-listed tools, (2) auto-
  // select the first allowed tool, (3) auto-fill a realistic sample payload
  // for that tool. Result/error from previous agent are cleared so clients
  // don't carry stale state across agents.
  useEffect(() => {
    if (!localAgentId) {
      setAllowedTools([])
      setTool('')
      setPayload('{}')
      return
    }
    setToolsLoading(true)
    setResult(null)
    setExecError(null)
    lastLoadedAgentRef.current = localAgentId
    registryService.listPermissions(localAgentId)
      .then((res) => {
        if (!mounted.current || lastLoadedAgentRef.current !== localAgentId) return
        const perms = res?.data || res || []
        const tools = Array.isArray(perms)
          ? perms
              .filter((p) => String(p.action || '').toUpperCase() !== 'DENY')
              .map((p) => p.tool_name || p.resource || p.tool)
              .filter(Boolean)
          : []
        const unique = [...new Set(tools)].sort()
        setAllowedTools(unique)
        // Pick the first allowed tool and seed its sample payload. Prefer a
        // "safe-looking" tool (no `delete`/`drop`/`exec`) to keep the
        // default action allow-y when a client just hits Execute.
        const firstSafe = unique.find((t) => !/delete|drop|exec|kill/i.test(t)) || unique[0] || ''
        setTool(firstSafe)
        setPayload(formatJson(fallbackPayload(firstSafe)))
        setPayloadError(null)
      })
      .catch(() => {
        if (mounted.current) {
          setAllowedTools([])
          setTool('')
        }
      })
      .finally(() => {
        if (mounted.current) setToolsLoading(false)
      })
  }, [localAgentId])

  // When the user picks a different tool, re-seed the payload from our
  // sample table. They can still edit it freely afterwards.
  const handleToolChange = (newTool) => {
    setTool(newTool)
    setPayload(formatJson(fallbackPayload(newTool)))
    setPayloadError(null)
  }

  const loadAttackScenario = (scenario) => {
    setTool(scenario.tool)
    setPayload(formatJson(scenario.payload))
    setPayloadError(null)
    setExecError(null)
  }

  const selectedAgentObj = useMemo(
    () => agents.find((a) => a.id === localAgentId) || null,
    [agents, localAgentId],
  )

  const validatePayload = (val) => {
    try { JSON.parse(val); setPayloadError(null); return true }
    catch (e) { setPayloadError(e.message); return false }
  }

  const execute = async () => {
    if (!selectedAgent) { setExecError('Select an agent'); return }
    if (!tool.trim())   { setExecError('Select or enter a tool'); return }
    if (!validatePayload(payload)) return

    setExecuting(true)
    setExecError(null)
    setResult(null)

    const startMs = Date.now()
    try {
      const data = await playgroundService.execute(selectedAgent, tool.trim(), JSON.parse(payload))
      const latency = Date.now() - startMs
      if (!mounted.current) return
      const decision = data?.action || data?.decision || 'allow'
      setHistory(prev => [{
        timestamp: new Date().toLocaleTimeString(),
        agent: selectedAgent, tool: tool.trim(), decision,
        risk: data?.risk ?? null, latency, status: 200,
      }, ...prev].slice(0, 10))
      setResult({ ...data, latency })
    } catch (err) {
      if (!mounted.current) return
      // Capture the status + reason so a "BLOCK 403" still renders the
      // decision card instead of looking like a generic error.
      const status = err?._status || 500
      const isBlock = status === 403
      const message = err?.message || 'Execution failed.'
      setResult({
        action:  isBlock ? 'block' : 'error',
        risk:    isBlock ? 1.0 : null,
        reasons: [message],
        latency: Date.now() - startMs,
        _error:  !isBlock,
      })
      setHistory(prev => [{
        timestamp: new Date().toLocaleTimeString(),
        agent: selectedAgent, tool: tool.trim(),
        decision: isBlock ? 'block' : 'error',
        risk: isBlock ? 1.0 : null,
        latency: Date.now() - startMs, status,
      }, ...prev].slice(0, 10))
      if (!isBlock) setExecError(message)
    } finally { if (mounted.current) setExecuting(false) }
  }

  const decMeta = DECISION_META[(result?.action || '').toLowerCase()] || DECISION_META.allow

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight">Agent Playground</h1>
          <p className="text-xs text-neutral-500 mt-1">
            Execute live tool calls against a real agent. Decisions are evaluated by the production
            policy engine and written to the audit chain — same code path as production traffic.
          </p>
        </div>
        <div className="text-[10px] font-mono text-neutral-600 uppercase tracking-wider">
          POST /execute
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
        {/* ── Input panel ── */}
        <div className="lg:col-span-2 space-y-4">
          <Card title="Request Config" icon={Terminal}>
            <div className="space-y-5">
              {/* Agent selector */}
              <div className="space-y-1.5">
                <label htmlFor="pg-agent" className="label-standard">Agent</label>
                {agentsLoading ? (
                  <SkeletonLoader variant="card" className="h-9" />
                ) : (
                  <select
                    id="pg-agent"
                    value={localAgentId}
                    onChange={(e) => {
                      setLocalAgentId(e.target.value)
                      setSelectedAgentId(e.target.value)
                    }}
                    className="input-standard h-9"
                  >
                    <option value="">— select agent —</option>
                    {agents.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name} · {(a.status || 'unknown').toLowerCase()}
                      </option>
                    ))}
                  </select>
                )}
                {selectedAgentObj && (
                  <p className="text-[10px] text-neutral-600 font-mono pt-1">
                    {selectedAgentObj.id.slice(0, 8)}… · {toolsLoading
                      ? 'loading allow-list…'
                      : `${allowedTools.length} tool${allowedTools.length === 1 ? '' : 's'} allowed`}
                  </p>
                )}
                {agents.length === 0 && !agentsLoading && (
                  <div className="mt-2 rounded-lg border border-amber-500/20 bg-amber-500/[0.05] p-3 space-y-2">
                    <p className="text-xs text-amber-300">
                      No agents registered — register one to use the playground.
                    </p>
                    <Link
                      to="/onboarding"
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-white text-black text-[11px] font-semibold hover:bg-neutral-200 transition-colors"
                    >
                      <Plus size={11} aria-hidden="true" /> Register your first agent
                    </Link>
                  </div>
                )}
              </div>

              {/* Tool dropdown — driven by the agent's allow-list */}
              <div className="space-y-1.5">
                <label htmlFor="pg-tool" className="label-standard flex items-center gap-1.5">
                  Tool
                  <Shield size={10} className="text-neutral-600" />
                  <span className="text-[10px] text-neutral-600 normal-case font-normal">from allow-list</span>
                </label>
                {toolsLoading ? (
                  <SkeletonLoader variant="card" className="h-9" />
                ) : allowedTools.length > 0 ? (
                  <select
                    id="pg-tool"
                    value={tool}
                    onChange={(e) => handleToolChange(e.target.value)}
                    className="input-standard h-9 font-mono"
                  >
                    {allowedTools.map((t) => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                  </select>
                ) : (
                  <input
                    id="pg-tool"
                    type="text"
                    value={tool}
                    onChange={(e) => setTool(e.target.value)}
                    placeholder={localAgentId ? 'No tools in agent allow-list — type a tool to test deny' : 'select an agent first'}
                    disabled={!localAgentId}
                    className="input-standard h-9 font-mono"
                  />
                )}
              </div>

              {/* Payload — auto-filled per tool, editable */}
              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <label htmlFor="pg-payload" className="label-standard flex items-center gap-1.5">
                    Payload (JSON)
                    <Wand2 size={10} className="text-neutral-600" />
                    <span className="text-[10px] text-neutral-600 normal-case font-normal">auto-filled</span>
                  </label>
                  {tool && (
                    <button
                      type="button"
                      onClick={() => {
                        setPayload(formatJson(fallbackPayload(tool)))
                        setPayloadError(null)
                      }}
                      className="text-[10px] text-neutral-500 hover:text-white transition-colors"
                    >
                      reset sample
                    </button>
                  )}
                </div>
                <textarea
                  id="pg-payload"
                  value={payload}
                  onChange={e => { setPayload(e.target.value); validatePayload(e.target.value) }}
                  rows={8}
                  spellCheck={false}
                  aria-invalid={!!payloadError}
                  aria-describedby={payloadError ? 'pg-payload-error' : undefined}
                  className={`input-standard py-2 font-mono resize-none text-xs ${payloadError ? 'border-red-500/40 focus:border-red-500/60' : ''}`}
                />
                {payloadError && (
                  <p id="pg-payload-error" className="text-xs text-red-400">Invalid JSON: {payloadError}</p>
                )}
              </div>

              {execError && (
                <div className="flex items-center gap-2 p-3 rounded-lg bg-red-500/5 border border-red-500/10 text-xs text-red-400" role="alert">
                  <AlertTriangle size={13} aria-hidden="true" /> {execError}
                </div>
              )}

              <button
                type="button"
                onClick={execute}
                disabled={executing || !!payloadError || !selectedAgent || !tool}
                aria-busy={executing}
                className="w-full h-10 rounded-lg bg-white text-black text-xs font-bold uppercase tracking-wide hover:bg-neutral-200 transition-colors disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center gap-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
              >
                {executing ? (
                  <><div className="w-3.5 h-3.5 border-2 border-black/30 border-t-black rounded-full animate-spin" aria-hidden="true" /> Executing…</>
                ) : (
                  <><Play size={13} aria-hidden="true" /> Execute</>
                )}
              </button>
            </div>
          </Card>

          {/* Attack scenarios — one-click hostile payloads to verify the
              hard-deny pipeline. Distinct visual treatment so a client
              never confuses them for safe defaults. */}
          {selectedAgent && (
            <Card title="Test scenarios" icon={Zap}>
              <p className="text-[11px] text-neutral-500 mb-3 leading-snug">
                Load a canonical hostile payload. The gateway should block each one with HTTP 403
                regardless of the agent's allow-list — the inference-proxy detectors run first.
              </p>
              <div className="space-y-2">
                {ATTACK_SCENARIOS.map((s) => (
                  <button
                    key={s.id}
                    type="button"
                    onClick={() => loadAttackScenario(s)}
                    className="w-full text-left p-2.5 rounded-lg border border-red-500/15 bg-red-500/[0.04] hover:bg-red-500/[0.08] hover:border-red-500/25 transition-colors group"
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-red-300 font-medium">{s.label}</span>
                      <span className="text-[9px] font-mono text-red-400/60 group-hover:text-red-300">
                        expect 403
                      </span>
                    </div>
                    <p className="text-[10px] text-neutral-600 font-mono mt-0.5 truncate">
                      {s.tool}
                    </p>
                  </button>
                ))}
              </div>
            </Card>
          )}
        </div>

        {/* ── Results panel ── */}
        <div className="lg:col-span-3 space-y-4">
          {result ? (
            <>
              {/* Decision verdict */}
              <div className={`p-5 rounded-xl border ${decMeta.bg} ${decMeta.border} space-y-4`}>
                <div className="flex items-center justify-between">
                  <div>
                    <p className="label-standard mb-1">Decision</p>
                    <p className={`text-3xl font-black tracking-tight ${decMeta.text}`}>{decMeta.label}</p>
                  </div>
                  <div className="text-right">
                    <p className="label-standard mb-1">Risk Score</p>
                    <p className={`text-2xl font-black font-mono ${
                      (result.risk || 0) >= 0.7 ? 'text-red-400' :
                      (result.risk || 0) >= 0.4 ? 'text-yellow-400' : 'text-green-400'
                    }`}>{(result.risk || 0).toFixed(3)}</p>
                  </div>
                </div>

                {result.signals && (
                  <div className="space-y-2 pt-3 border-t border-white/[0.05]">
                    <p className="label-standard">Signal breakdown</p>
                    {Object.entries(SIGNAL_COLORS).map(([k, c]) => (
                      <SignalBar key={k} label={k} value={result.signals?.[k]} color={c} />
                    ))}
                  </div>
                )}

                {Array.isArray(result.reasons) && result.reasons.length > 0 && (
                  <div className="space-y-1 pt-3 border-t border-white/[0.05]">
                    <p className="label-standard">Reasons</p>
                    <ul className="space-y-0.5">
                      {result.reasons.slice(0, 5).map((r, i) => (
                        <li key={i} className="text-xs text-neutral-300 font-mono">• {r}</li>
                      ))}
                    </ul>
                  </div>
                )}

                <div className="flex items-center gap-4 text-xs text-neutral-500 pt-3 border-t border-white/[0.05]">
                  <span className="flex items-center gap-1.5"><Clock size={11} /> {result.latency}ms</span>
                  {result.request_id && (
                    <span className="font-mono text-neutral-600">req {result.request_id.slice(0, 8)}…</span>
                  )}
                </div>
              </div>

              {/* Raw JSON */}
              <Card
                title="Raw response"
                icon={Terminal}
                action={
                  <button
                    type="button"
                    onClick={() => setRawOpen((o) => !o)}
                    className="flex items-center gap-1 text-[10px] text-neutral-500 hover:text-white"
                  >
                    {rawOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                    {rawOpen ? 'Hide' : 'Show'}
                  </button>
                }
              >
                {rawOpen && (
                  <div className="relative">
                    <CopyButton text={JSON.stringify(result, null, 2)} />
                    <pre className="text-[11px] font-mono text-neutral-400 bg-black/40 rounded-lg p-3 overflow-x-auto max-h-72">
                      {JSON.stringify(result, null, 2)}
                    </pre>
                  </div>
                )}
              </Card>
            </>
          ) : (
            <Card title="No execution yet" icon={Terminal}>
              <div className="flex flex-col items-center justify-center py-16 text-center">
                {agents.length === 0 && !agentsLoading ? (
                  <>
                    <Bot size={28} className="text-neutral-700 mb-3" aria-hidden="true" />
                    <p className="text-sm text-neutral-400">No agents registered yet</p>
                    <p className="text-xs text-neutral-600 mt-1 max-w-sm">
                      Register your first agent via the Onboarding Wizard, then return here to
                      test live tool calls against the production policy engine.
                    </p>
                    <Link
                      to="/onboarding"
                      className="mt-4 inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-white text-black text-xs font-semibold hover:bg-neutral-200 transition-colors"
                    >
                      <Plus size={13} aria-hidden="true" /> Register your first agent
                    </Link>
                  </>
                ) : (
                  <>
                    <Terminal size={28} className="text-neutral-700 mb-3" aria-hidden="true" />
                    <p className="text-sm text-neutral-500">
                      Select an agent → pick a tool from its allow-list → Execute.
                    </p>
                    <p className="text-xs text-neutral-600 mt-1">
                      Use the <span className="text-red-400">Test scenarios</span> card to verify hard-deny pipelines.
                    </p>
                  </>
                )}
              </div>
            </Card>
          )}

          {/* History */}
          {history.length > 0 && (
            <Card title="Recent executions" icon={Clock}>
              <div className="space-y-1.5">
                {history.map((h, i) => {
                  const m = DECISION_META[(h.decision || '').toLowerCase()] || DECISION_META.allow
                  return (
                    <div key={i} className="flex items-center gap-3 text-xs py-1.5 px-2 hover:bg-white/[0.02] rounded">
                      <span className="text-neutral-600 font-mono w-16">{h.timestamp}</span>
                      <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold ${m.bg} ${m.text} w-16 text-center`}>
                        {m.label}
                      </span>
                      <span className="text-neutral-400 font-mono truncate flex-1">{h.tool}</span>
                      {h.risk != null && (
                        <span className="text-neutral-500 font-mono w-12 text-right">
                          {h.risk.toFixed(2)}
                        </span>
                      )}
                      <span className="text-neutral-600 font-mono w-14 text-right">{h.latency}ms</span>
                    </div>
                  )
                })}
              </div>
            </Card>
          )}
        </div>
      </div>
    </div>
  )
}
