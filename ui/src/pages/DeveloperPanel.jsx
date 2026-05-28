import React, { useState, useEffect, useRef } from 'react'
import { Code2, Key, Copy, Check, Plus, Trash2, Eye, EyeOff, Terminal, Book, RefreshCw, AlertTriangle } from 'lucide-react'
import Card from '../components/Common/Card'
import SkeletonLoader from '../components/Common/SkeletonLoader'
import Modal from '../components/Common/Modal'
import Button from '../components/Common/Button'
import { api } from '../services/api'
import { useAuth } from '../hooks/useAuth'

const GW = typeof window !== 'undefined'
  ? (import.meta.env.VITE_GATEWAY_URL || window.location.origin)
  : 'http://localhost:8000'

/* ── Copy button ────────────────────────────────────────────────────────────── */
function CopyButton({ text, className = '' }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }
  return (
    <button
      type="button"
      onClick={copy}
      aria-label={copied ? 'Copied' : 'Copy to clipboard'}
      className={`p-1.5 rounded transition-colors ${copied ? 'text-green-400' : 'text-neutral-500 hover:text-white'} ${className}`}
    >
      {copied ? <Check size={12} aria-hidden="true" /> : <Copy size={12} aria-hidden="true" />}
    </button>
  )
}

/* ── Code block ─────────────────────────────────────────────────────────────── */
function CodeBlock({ code, language = 'bash' }) {
  return (
    <div className="relative rounded-xl bg-black/40 border border-white/5 overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2 border-b border-white/5">
        <span className="label-standard">{language}</span>
        <CopyButton text={code} />
      </div>
      <pre className="px-4 py-3 text-xs font-mono text-green-400 overflow-x-auto whitespace-pre-wrap leading-relaxed">
        {code}
      </pre>
    </div>
  )
}

/* ── Constants ──────────────────────────────────────────────────────────────── */
const TABS = ['API Keys', 'cURL Examples', 'SDK Guide', 'Endpoint Reference']

const ENDPOINTS = [
  { method: 'POST',   path: '/auth/token',                  auth: false, description: 'User login — returns JWT' },
  { method: 'POST',   path: '/auth/refresh',                auth: true,  description: 'Refresh access token' },
  { method: 'POST',   path: '/auth/logout',                 auth: true,  description: 'Invalidate session' },
  { method: 'GET',    path: '/agents',                      auth: true,  description: 'List all agents' },
  { method: 'POST',   path: '/agents',                      auth: true,  description: 'Register new agent' },
  { method: 'GET',    path: '/agents/:id',                  auth: true,  description: 'Get agent details' },
  { method: 'PATCH',  path: '/agents/:id',                  auth: true,  description: 'Update agent' },
  { method: 'DELETE', path: '/agents/:id',                  auth: true,  description: 'Delete agent' },
  { method: 'POST',   path: '/agents/:id/permissions',      auth: true,  description: 'Grant tool permission' },
  { method: 'POST',   path: '/execute',                     auth: true,  description: 'Execute tool through decision engine' },
  { method: 'GET',    path: '/audit/logs',                  auth: true,  description: 'List audit log entries' },
  { method: 'POST',   path: '/audit/logs/search',           auth: true,  description: 'Search/filter audit logs' },
  { method: 'GET',    path: '/audit/logs/verify',           auth: true,  description: 'Verify cryptographic chain integrity' },
  { method: 'GET',    path: '/risk/summary',                auth: true,  description: 'Risk summary metrics' },
  { method: 'GET',    path: '/risk/timeline',               auth: true,  description: '7-day risk timeline' },
  { method: 'GET',    path: '/decision/history',            auth: true,  description: 'Recent decision history' },
  { method: 'GET',    path: '/decision/kill-switch/:tid',   auth: true,  description: 'Kill switch status' },
  { method: 'POST',   path: '/decision/kill-switch/:tid',   auth: true,  description: 'Engage kill switch (ADMIN/SECURITY)' },
  { method: 'DELETE', path: '/decision/kill-switch/:tid',   auth: true,  description: 'Disengage kill switch' },
  { method: 'GET',    path: '/forensics/replay/:agentId',   auth: true,  description: 'Forensic replay for agent' },
  { method: 'GET',    path: '/forensics/investigation',     auth: true,  description: 'List high-risk investigations' },
  { method: 'GET',    path: '/billing/summary',             auth: true,  description: 'Billing and ROI summary' },
  { method: 'GET',    path: '/api-keys',                    auth: true,  description: 'List API keys' },
  { method: 'POST',   path: '/api-keys',                    auth: true,  description: 'Create API key' },
  { method: 'DELETE', path: '/api-keys/:id',                auth: true,  description: 'Revoke API key' },
]

const METHOD_COLORS = {
  GET:    'text-green-400  bg-green-500/10  border-green-500/20',
  POST:   'text-blue-400   bg-blue-500/10   border-blue-500/20',
  PATCH:  'text-yellow-400 bg-yellow-500/10 border-yellow-500/20',
  DELETE: 'text-red-400    bg-red-500/10    border-red-500/20',
}

/* ── Component ──────────────────────────────────────────────────────────────── */
export default function DeveloperPanel() {
  const { tenant_id } = useAuth()
  const mounted = useRef(true)
  const [tab,         setTab]         = useState(0)
  const [apiKeys,     setApiKeys]     = useState([])
  const [keysLoading, setKeysLoading] = useState(true)
  const [creating,    setCreating]    = useState(false)
  const [newKeyName,  setNewKeyName]  = useState('')
  const [showCreate,  setShowCreate]  = useState(false)
  const [visibleKeys, setVisibleKeys] = useState({})
  const [revoking,    setRevoking]    = useState({})
  const [revokeTarget, setRevokeTarget] = useState(null)
  const [keysError,   setKeysError]   = useState('')
  const [createError, setCreateError] = useState('')

  const tid              = tenant_id || '00000000-0000-0000-0000-000000000001'
  // Demo agent IDs from the production seed — keep in sync with seed_demo_data.py.
  const DEMO_AGENT       = 'a245cc68-19aa-48a7-8862-f3d7f0332ff6'   // demo-agent
  const DB_AGENT         = 'a0c1849b-3b60-40aa-a7ef-35557a7ceef6'   // db-copilot-demo
  const SUPPORT_AGENT    = 'd4f0fbfc-d629-4acd-ac82-30787f0c0f2a'   // support-agent-demo
  const DEVOPS_AGENT     = '37533cba-54a2-475c-94e2-319c3dfdf69e'   // devops-agent-demo
  // For copy-paste safety we use a placeholder in headers — the user pipes it
  // in from Step 1 (the login response) via the $TOKEN env var.
  const tokenPlaceholder = '$TOKEN'

  const loadKeys = () => {
    setKeysLoading(true)
    setKeysError('')
    api.getApiKeys()
      .then(res => { if (mounted.current) setApiKeys(res?.data || res || []) })
      .catch(err => {
        // 2026-05-14: do NOT clear the list on transient errors — that made
        // existing keys vanish from the UI on every blip. Surface the error
        // banner instead and leave the previous list in place.
        if (mounted.current) setKeysError(err?.message || 'Failed to load API keys.')
      })
      .finally(() => { if (mounted.current) setKeysLoading(false) })
  }

  useEffect(() => {
    mounted.current = true
    loadKeys()
    return () => { mounted.current = false }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const createKey = async () => {
    if (!newKeyName.trim()) return
    setCreating(true)
    setCreateError('')
    try {
      await api.createApiKey({ name: newKeyName.trim() })
      setNewKeyName('')
      setShowCreate(false)
      loadKeys()
    } catch (err) {
      // Surface real failure; previously `catch {}` silently dropped the create.
      if (mounted.current) setCreateError(err?.message || 'Failed to create key.')
    }
    finally { if (mounted.current) setCreating(false) }
  }

  const revokeKey = async () => {
    if (!revokeTarget) return
    setRevoking(r => ({ ...r, [revokeTarget.id]: true }))
    try {
      await api.revokeApiKey(revokeTarget.id)
      loadKeys()
    } catch (err) {
      if (mounted.current) setKeysError(err?.message || 'Revoke failed.')
    }
    finally {
      if (mounted.current) setRevoking(r => ({ ...r, [revokeTarget.id]: false }))
      setRevokeTarget(null)
    }
  }

  const CURL_EXAMPLES = [
    {
      title: 'Step 1 — Login (get JWT)',
      desc:  'Returns a 15-minute access token in `data.access_token`. The same value is set as the httpOnly `acp_token` cookie for browser SDKs.',
      code: `TOKEN=$(curl -s -X POST ${GW}/auth/token \\
  -H "Content-Type: application/json" \\
  -H "X-Tenant-ID: ${tid}" \\
  -d '{"email":"demo@aegisagent.in","password":"demo1234"}' \\
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['access_token'])")

echo "Token: \${TOKEN:0:40}..."`,
    },
    {
      title: 'Step 2 — List registered agents',
      desc:  'All demo agents pre-seeded in this tenant. Use these IDs for the calls below.',
      code: `curl -s "${GW}/agents?size=10" \\
  -H "Authorization: Bearer ${tokenPlaceholder}" \\
  -H "X-Tenant-ID: ${tid}" \\
  | python3 -m json.tool`,
    },
    {
      title: 'Step 3 — Safe tool call → ALLOWED',
      desc:  'demo-agent has `search_web` in its allow-list. Risk-scored, allowed.',
      code: `curl -s -X POST ${GW}/execute \\
  -H "Authorization: Bearer ${tokenPlaceholder}" \\
  -H "X-Tenant-ID: ${tid}" \\
  -H "Content-Type: application/json" \\
  -d '{"agent_id":"${DEMO_AGENT}","tool":"search_web","parameters":{"query":"AI governance"}}' \\
  | python3 -m json.tool

# Expected: {"action":"allow","risk":0.11,"signals":{...},"reasons":[...]}`,
    },
    {
      title: 'Step 4 — PII exfiltration attempt → BLOCKED (403)',
      desc:  'Inference Proxy detects SSN/credit-card patterns in tool input and hard-denies before the tool runs.',
      code: `curl -s -X POST ${GW}/execute \\
  -H "Authorization: Bearer ${tokenPlaceholder}" \\
  -H "X-Tenant-ID: ${tid}" \\
  -H "Content-Type: application/json" \\
  -d '{"agent_id":"${DEMO_AGENT}","tool":"send_email","parameters":{"body":"Customer SSN is 123-45-6789, DOB 01/01/1985"}}'

# Expected: HTTP 403 {"error":"Security: PII or credential data detected ..."}`,
    },
    {
      title: 'Step 5 — RCE attempt → BLOCKED (403)',
      desc:  'Dangerous code patterns (rm -rf, os.system, etc.) blocked by the RCE detector.',
      code: `curl -s -X POST ${GW}/execute \\
  -H "Authorization: Bearer ${tokenPlaceholder}" \\
  -H "X-Tenant-ID: ${tid}" \\
  -H "Content-Type: application/json" \\
  -d '{"agent_id":"${DEMO_AGENT}","tool":"run_code","parameters":{"exec":"os.system(\\"rm -rf /\\")"}}'

# Expected: HTTP 403 {"error":"Security: Dangerous code pattern detected ..."}`,
    },
    {
      title: 'Step 6 — SQL injection → BLOCKED (403)',
      desc:  'db-copilot-demo has run_query allowed, but stacked statements / DROP TABLE / boolean blind injection are detected before execution.',
      code: `curl -s -X POST ${GW}/execute \\
  -H "Authorization: Bearer ${tokenPlaceholder}" \\
  -H "X-Tenant-ID: ${tid}" \\
  -H "Content-Type: application/json" \\
  -d '{"agent_id":"${DB_AGENT}","tool":"run_query","parameters":{"query":"SELECT * FROM users WHERE 1=1; DROP TABLE users; --"}}'

# Expected: HTTP 403 {"error":"Security: SQL injection detected ..."}`,
    },
    {
      title: 'Step 7 — Destructive k8s op → BLOCKED (403)',
      desc:  'devops-agent-demo can run kubectl_get/delete, but the destructive-namespace detector blocks production-class targets and broad selectors.',
      code: `curl -s -X POST ${GW}/execute \\
  -H "Authorization: Bearer ${tokenPlaceholder}" \\
  -H "X-Tenant-ID: ${tid}" \\
  -H "Content-Type: application/json" \\
  -d '{"agent_id":"${DEVOPS_AGENT}","tool":"kubectl_delete","parameters":{"resource":"all","namespace":"production"}}'

# Expected: HTTP 403 {"error":"Security: destructive k8s op on production namespace"}`,
    },
    {
      title: 'Step 8 — Read the audit trail',
      desc:  'Every allow + block has a SHA-256 hash chained to the previous row. Tamper-evident, signed with ed25519.',
      code: `curl -s "${GW}/audit/logs?limit=5" \\
  -H "Authorization: Bearer ${tokenPlaceholder}" \\
  -H "X-Tenant-ID: ${tid}" \\
  | python3 -c "import sys,json; d=json.load(sys.stdin)['data']; print('total:',d['total']); [print(f\\\"  {i['decision']:8}{i['tool'] or '-':25} hash={i.get('event_hash','-')[:24]}...\\\") for i in d['items']]"`,
    },
    {
      title: 'Step 9 — Verify chain integrity',
      desc:  'Walks the entire chain server-side and reports any tampered or skipped rows.',
      code: `curl -s "${GW}/audit/logs/verify" \\
  -H "Authorization: Bearer ${tokenPlaceholder}" \\
  -H "X-Tenant-ID: ${tid}" \\
  | python3 -c "import sys,json; d=json.load(sys.stdin)['data']; print(f\\\"chain valid: {d['valid']}  processed: {d['processed_count']}  violations: {d['error_count']}\\\")"`,
    },
    {
      title: 'Step 10 — Stream live events (SSE)',
      desc:  'Long-poll Server-Sent Events. Run this in one tab, then trigger /execute in another and watch the events arrive in real time.',
      code: `curl -N "${GW}/events/stream?token=\${TOKEN}"

# Each line of output is:
#   event: connected         (initial handshake)
#   data: {...payload...}    (tool_executed, policy_decision, etc.)
#   event: heartbeat         (every 15s)`,
    },
    {
      title: 'Step 11 — System health',
      desc:  '12 service status snapshot — used by the ALB target check.',
      code: `curl -s ${GW}/system/health | python3 -m json.tool`,
    },
    {
      title: 'Step 12 — Risk summary',
      desc:  'Tenant-wide block rate, high-risk agents, signal weights.',
      code: `curl -s ${GW}/risk/summary \\
  -H "Authorization: Bearer ${tokenPlaceholder}" \\
  -H "X-Tenant-ID: ${tid}" \\
  | python3 -m json.tool`,
    },
  ]

  const SDK_PYTHON = `# pip install httpx
# Real working sample against ${GW}
import asyncio, httpx, os

GATEWAY  = "${GW}"
TENANT   = "${tid}"
EMAIL    = "demo@aegisagent.in"
PASSWORD = "demo1234"
AGENT_ID = "${DEMO_AGENT}"  # demo-agent

async def main():
    async with httpx.AsyncClient(base_url=GATEWAY, timeout=10.0) as c:
        # 1) Login
        r = await c.post(
            "/auth/token",
            json={"email": EMAIL, "password": PASSWORD},
            headers={"X-Tenant-ID": TENANT},
        )
        r.raise_for_status()
        token = r.json()["data"]["access_token"]
        H = {
            "Authorization": f"Bearer {token}",
            "X-Tenant-ID":   TENANT,
            "Content-Type":  "application/json",
        }

        # 2) Safe call (expect allow)
        ok = await c.post("/execute", headers=H, json={
            "agent_id": AGENT_ID,
            "tool":     "search_web",
            "parameters": {"query": "AI governance"},
        })
        print("safe call:", ok.status_code, ok.json().get("action"), ok.json().get("risk"))

        # 3) Hostile call (expect 403)
        bad = await c.post("/execute", headers=H, json={
            "agent_id": AGENT_ID,
            "tool":     "send_email",
            "parameters": {"body": "SSN is 123-45-6789"},
        })
        print("PII attempt:", bad.status_code, bad.json().get("error"))

asyncio.run(main())`

  const SDK_JS = `// Real working sample against ${GW}
// Node 18+ has fetch built in.
const GATEWAY  = '${GW}';
const TENANT   = '${tid}';
const EMAIL    = 'demo@aegisagent.in';
const PASSWORD = 'demo1234';
const AGENT_ID = '${DEMO_AGENT}'; // demo-agent

(async () => {
  // 1) Login
  const login = await fetch(\`\${GATEWAY}/auth/token\`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Tenant-ID': TENANT },
    body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
  }).then(r => r.json());
  const token = login.data.access_token;
  const H = {
    Authorization: \`Bearer \${token}\`,
    'X-Tenant-ID':  TENANT,
    'Content-Type': 'application/json',
  };

  // 2) Safe call (expect allow)
  const ok = await fetch(\`\${GATEWAY}/execute\`, {
    method: 'POST', headers: H,
    body: JSON.stringify({
      agent_id: AGENT_ID,
      tool: 'search_web',
      parameters: { query: 'AI governance' },
    }),
  }).then(r => r.json());
  console.log('safe call:', ok.action, 'risk=', ok.risk);

  // 3) Hostile call (expect 403)
  const bad = await fetch(\`\${GATEWAY}/execute\`, {
    method: 'POST', headers: H,
    body: JSON.stringify({
      agent_id: AGENT_ID,
      tool: 'send_email',
      parameters: { body: 'SSN is 123-45-6789' },
    }),
  });
  console.log('PII attempt:', bad.status, await bad.text());
})();`

  return (
    <div className="space-y-6 animate-fade-in">
      {/* ── Header ── */}
      <div className="page-header">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight">Developer Panel</h1>
          <p className="text-xs text-neutral-500 mt-0.5">API keys, integration examples, and endpoint reference</p>
        </div>
      </div>

      {/* ── Tabs ── */}
      <div
        className="flex gap-1 p-1 bg-[#080808] border border-white/5 rounded-xl w-fit"
        role="tablist"
        aria-label="Developer panel sections"
      >
        {TABS.map((t, i) => (
          <button
            key={i}
            type="button"
            role="tab"
            aria-selected={tab === i}
            onClick={() => setTab(i)}
            className={`px-4 py-2 rounded-lg text-xs font-bold transition-colors ${
              tab === i ? 'bg-white text-black' : 'text-neutral-500 hover:text-white'
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {/* ── API Keys ── */}
      {tab === 0 && (
        <Card title="API Keys" icon={Key}>
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <p className="text-xs text-neutral-500">API keys allow programmatic access to the gateway.</p>
              <Button size="sm" onClick={() => setShowCreate(!showCreate)}>
                <Plus size={13} aria-hidden="true" /> New Key
              </Button>
            </div>

            {showCreate && (
              <div className="flex items-center gap-3 p-4 rounded-xl bg-white/[0.02] border border-white/5">
                <input
                  type="text"
                  value={newKeyName}
                  onChange={e => setNewKeyName(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && createKey()}
                  placeholder="Key name (e.g. prod-service)"
                  aria-label="New API key name"
                  className="input-standard flex-1 h-9 font-mono"
                />
                <Button
                  size="sm"
                  loading={creating}
                  disabled={creating || !newKeyName.trim()}
                  onClick={createKey}
                >
                  {creating ? 'Creating…' : 'Create'}
                </Button>
              </div>
            )}

            {createError && (
              <div className="mb-3 px-3 py-2 rounded-lg border border-red-500/30 bg-red-500/10 text-xs text-red-400" role="alert">
                {createError}
              </div>
            )}
            {keysError && (
              <div className="mb-3 flex items-center justify-between gap-3 px-3 py-2 rounded-lg border border-red-500/30 bg-red-500/10" role="alert">
                <span className="text-xs text-red-400">{keysError}</span>
                <button onClick={loadKeys} className="text-xs text-red-300 underline">Retry</button>
              </div>
            )}
            {keysLoading ? (
              <SkeletonLoader variant="row" count={3} />
            ) : apiKeys.length === 0 ? (
              <div className="flex items-center justify-center h-20 text-neutral-600 text-xs">No API keys yet</div>
            ) : (
              <div className="space-y-2">
                {apiKeys.map(k => (
                  <div key={k.id} className="flex items-center gap-4 p-3 rounded-xl bg-white/[0.02] border border-white/5">
                    <div className="flex-1 min-w-0">
                      <p className="text-xs font-semibold text-white">{k.name}</p>
                      <p className="text-xs text-neutral-600 font-mono mt-0.5">
                        {visibleKeys[k.id] ? (k.key || k.id) : '••••••••••••••••••••••••••••••••'}
                      </p>
                    </div>
                    <span className={`text-xs font-bold ${k.status === 'active' ? 'text-green-400' : 'text-neutral-500'}`}>
                      {k.status || 'active'}
                    </span>
                    <p className="text-xs text-neutral-600 hidden sm:block">
                      {k.created_at ? new Date(k.created_at).toLocaleDateString() : '—'}
                    </p>
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => setVisibleKeys(v => ({ ...v, [k.id]: !v[k.id] }))}
                        aria-label={visibleKeys[k.id] ? 'Hide key' : 'Show key'}
                        className="p-1.5 text-neutral-500 hover:text-white transition-colors"
                      >
                        {visibleKeys[k.id] ? <EyeOff size={13} aria-hidden="true" /> : <Eye size={13} aria-hidden="true" />}
                      </button>
                      <CopyButton text={k.key || k.id} />
                      <button
                        type="button"
                        onClick={() => setRevokeTarget({ id: k.id, name: k.name })}
                        disabled={revoking[k.id]}
                        aria-label={`Revoke API key ${k.name}`}
                        className="p-1.5 text-neutral-500 hover:text-red-400 transition-colors disabled:opacity-40"
                      >
                        <Trash2 size={13} aria-hidden="true" />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </Card>
      )}

      {/* ── cURL Examples ── */}
      {tab === 1 && (
        <div className="space-y-5">
          <div className="rounded-xl border border-blue-500/20 bg-blue-500/[0.05] p-4 text-xs text-neutral-300">
            <p className="font-semibold text-blue-300 mb-1">Live tutorial against this deployment</p>
            <p className="text-neutral-400">
              Every snippet below runs against <code className="text-blue-300 font-mono">{GW}</code> with the demo tenant and seeded agent IDs. Start with Step 1 — it sets a <code className="text-blue-300 font-mono">$TOKEN</code> env var the later steps reuse. Each block has a copy button; paste straight into a Mac / Linux / WSL terminal.
            </p>
          </div>
          {CURL_EXAMPLES.map((ex, i) => (
            <div key={i}>
              <p className="text-xs font-bold text-neutral-400 mb-1 uppercase tracking-widest">{ex.title}</p>
              {ex.desc && (
                <p className="text-[11px] text-neutral-500 mb-2 leading-snug">{ex.desc}</p>
              )}
              <CodeBlock code={ex.code} language="bash" />
            </div>
          ))}
        </div>
      )}

      {/* ── SDK Guide ── */}
      {tab === 2 && (
        <div className="space-y-8">
          {/* ── Framework integrations — 3-line install ── */}
          <div>
            <p className="text-xs font-bold text-neutral-400 mb-3 uppercase tracking-widest">Framework Integrations — 3-Line Install</p>
            <div className="grid grid-cols-1 gap-4">
              {[
                {
                  label: 'LangChain',
                  install: 'pip install aegis-langchain',
                  code: `from aegis_langchain import AegisMiddleware\nagent = AegisMiddleware(my_langchain_agent, api_key="acp_...")\nresult = agent.invoke({"input": "analyze /etc/passwd"})  # automatically blocked`,
                },
                {
                  label: 'OpenAI',
                  install: 'pip install aegis-openai',
                  code: `from aegis_openai import AegisOpenAI\nclient = AegisOpenAI(aegis_key="acp_...", tenant_id="${tid}")\nresponse = client.chat.completions.create(model="gpt-4o", messages=[...], tools=[...])`,
                },
                {
                  label: 'Anthropic / Claude',
                  install: 'pip install aegis-anthropic',
                  code: `from aegis_anthropic import AegisAnthropic\nclient = AegisAnthropic(aegis_key="acp_...", tenant_id="${tid}")\nresponse = client.messages.create(model="claude-opus-4-7", max_tokens=1024, tools=[...], messages=[...])`,
                },
              ].map(({ label, install, code }) => (
                <div key={label} className="rounded-xl border border-white/5 bg-white/[0.01] overflow-hidden">
                  <div className="flex items-center justify-between px-4 py-2 border-b border-white/5 bg-white/[0.02]">
                    <span className="text-xs font-bold text-white">{label}</span>
                    <CopyButton text={install} />
                  </div>
                  <div className="px-4 py-2 border-b border-white/5 bg-black/30">
                    <code className="text-[11px] font-mono text-amber-400">{install}</code>
                  </div>
                  <pre className="px-4 py-3 text-[11px] font-mono text-green-400 overflow-x-auto whitespace-pre leading-relaxed">{code}</pre>
                </div>
              ))}
            </div>
            <p className="mt-2 text-[10px] text-neutral-600">
              All three packages wrap the <code>/execute</code> endpoint. Blocked tool calls return a descriptive message instead of executing — your agent handles it naturally.
            </p>
          </div>

          {/* ── Low-level SDKs ── */}
          <div>
            <p className="text-xs font-bold text-neutral-400 mb-2 uppercase tracking-widest">Low-Level Python SDK</p>
            <div className="p-3 rounded-xl bg-blue-500/5 border border-blue-500/10 text-xs text-blue-300 mb-3">
              The ACP SDK wraps the gateway API with automatic authentication, idempotency keys, and typed error handling.
            </div>
            <CodeBlock code={SDK_PYTHON} language="python" />
          </div>
          <div>
            <p className="text-xs font-bold text-neutral-400 mb-2 uppercase tracking-widest">JavaScript SDK</p>
            <CodeBlock code={SDK_JS} language="javascript" />
          </div>

          <Card title="Required Headers">
            <div className="space-y-0 text-xs font-mono">
              {[
                ['Authorization',  'Bearer <JWT or acp_key>',             'JWT from /auth/token or API key created above'],
                ['X-Tenant-ID',    '<uuid>',                              'Tenant isolation — must match JWT claim'],
                ['X-Agent-ID',     '<agent-uuid or "dashboard-agent">',   'Agent context for policy evaluation'],
                ['X-Request-ID',   '<uuid>',                              'Distributed tracing (auto-generated by SDK)'],
                ['Content-Type',   'application/json',                    'Required for POST/PATCH requests'],
              ].map(([name, val, desc]) => (
                <div key={name} className="flex gap-4 py-2.5 border-b border-white/[0.04] last:border-0">
                  <span className="text-white w-32 shrink-0">{name}</span>
                  <span className="text-green-400 flex-1">{val}</span>
                  <span className="text-neutral-600 hidden md:block">{desc}</span>
                </div>
              ))}
            </div>
          </Card>
        </div>
      )}

      {/* ── Endpoint Reference ── */}
      {tab === 3 && (
        <Card title="API Endpoint Reference" icon={Book}>
          <div className="table-scroll">
            <table className="table-base min-w-[600px]" role="table">
              <thead>
                <tr>
                  {['Method', 'Path', 'Auth', 'Description'].map(h => (
                    <th key={h} className="table-th first:pl-5">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {ENDPOINTS.map((ep, i) => (
                  <tr key={i} className="table-row">
                    <td className="table-td first:pl-5">
                      <span className={`status-badge ${METHOD_COLORS[ep.method] ?? 'text-neutral-400 bg-white/5 border-white/10'}`}>
                        {ep.method}
                      </span>
                    </td>
                    <td className="table-td font-mono text-white">{ep.path}</td>
                    <td className="table-td">
                      <span className={`text-xs font-bold ${ep.auth ? 'text-yellow-400' : 'text-green-400'}`}>
                        {ep.auth ? 'JWT' : 'None'}
                      </span>
                    </td>
                    <td className="table-td text-neutral-400">{ep.description}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {/* ── Revoke confirmation modal ── */}
      <Modal
        isOpen={!!revokeTarget}
        title="Revoke API Key"
        onClose={() => setRevokeTarget(null)}
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setRevokeTarget(null)}>Cancel</Button>
            <Button
              variant="danger"
              size="sm"
              loading={revoking[revokeTarget?.id]}
              onClick={revokeKey}
            >
              Revoke Key
            </Button>
          </>
        }
      >
        <p className="text-sm text-neutral-300">
          Revoke API key <span className="font-bold text-white">"{revokeTarget?.name}"</span>?
        </p>
        <p className="text-xs text-neutral-500 mt-2">
          This cannot be undone. Any service using this key will lose access immediately.
        </p>
      </Modal>
    </div>
  )
}
