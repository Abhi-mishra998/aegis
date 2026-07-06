import{r as i,f as e}from"./vendor-react-NHDOn6vd.js";import{b as te,C as A,B as p,S as ae,M as se,l as v}from"./index-CWONLu3I.js";import{aj as B,Q as z,E as ne,g as ie,ag as re,aD as oe,n as le,y as de}from"./vendor-icons-5QlxSFhX.js";import"./vendor-clerk-CmDOaSlB.js";import"./vendor-DPOnuCVU.js";import"./vendor-router-D2KVpLGi.js";const s=typeof window<"u"?window.location.origin:"http://localhost:8000";function E({text:h,className:n=""}){const[l,m]=i.useState(!1),x=()=>{navigator.clipboard.writeText(h).then(()=>{m(!0),setTimeout(()=>m(!1),2e3)})};return e.jsx("button",{type:"button",onClick:x,"aria-label":l?"Copied":"Copy to clipboard",className:`p-1.5 rounded transition-colors ${l?"text-green-400":"text-neutral-500 hover:text-white"} ${n}`,children:l?e.jsx(le,{size:12,"aria-hidden":"true"}):e.jsx(de,{size:12,"aria-hidden":"true"})})}function k({code:h,language:n="bash"}){return e.jsxs("div",{className:"relative rounded-xl bg-black/40 border border-white/5 overflow-hidden",children:[e.jsxs("div",{className:"flex items-center justify-between px-4 py-2 border-b border-white/5",children:[e.jsx("span",{className:"label-standard",children:n}),e.jsx(E,{text:h})]}),e.jsx("pre",{className:"px-4 py-3 text-xs font-mono text-green-400 overflow-x-auto whitespace-pre-wrap leading-relaxed",children:h})]})}const ce=["API Keys","cURL Examples","SDK Guide","Endpoint Reference"],he=[{method:"POST",path:"/auth/token",auth:!1,description:"User login — returns JWT"},{method:"POST",path:"/auth/refresh",auth:!0,description:"Refresh access token"},{method:"POST",path:"/auth/logout",auth:!0,description:"Invalidate session"},{method:"GET",path:"/agents",auth:!0,description:"List all agents"},{method:"POST",path:"/agents",auth:!0,description:"Register new agent"},{method:"GET",path:"/agents/:id",auth:!0,description:"Get agent details"},{method:"PATCH",path:"/agents/:id",auth:!0,description:"Update agent"},{method:"DELETE",path:"/agents/:id",auth:!0,description:"Delete agent"},{method:"POST",path:"/agents/:id/permissions",auth:!0,description:"Grant tool permission"},{method:"POST",path:"/execute",auth:!0,description:"Execute tool through decision engine"},{method:"GET",path:"/audit/logs",auth:!0,description:"List audit log entries"},{method:"POST",path:"/audit/logs/search",auth:!0,description:"Search/filter audit logs"},{method:"GET",path:"/audit/logs/verify",auth:!0,description:"Verify cryptographic chain integrity"},{method:"GET",path:"/risk/summary",auth:!0,description:"Risk summary metrics"},{method:"GET",path:"/risk/timeline",auth:!0,description:"7-day risk timeline"},{method:"GET",path:"/decision/history",auth:!0,description:"Recent decision history"},{method:"GET",path:"/decision/kill-switch/:tid",auth:!0,description:"Kill switch status"},{method:"POST",path:"/decision/kill-switch/:tid",auth:!0,description:"Engage kill switch (ADMIN/SECURITY)"},{method:"DELETE",path:"/decision/kill-switch/:tid",auth:!0,description:"Disengage kill switch"},{method:"GET",path:"/forensics/replay/:agentId",auth:!0,description:"Forensic replay for agent"},{method:"GET",path:"/forensics/investigation",auth:!0,description:"List high-risk investigations"},{method:"GET",path:"/billing/summary",auth:!0,description:"Billing and ROI summary"},{method:"GET",path:"/api-keys",auth:!0,description:"List API keys"},{method:"POST",path:"/api-keys",auth:!0,description:"Create API key"},{method:"DELETE",path:"/api-keys/:id",auth:!0,description:"Revoke API key"}],pe={GET:"text-green-400  bg-green-500/10  border-green-500/20",POST:"text-blue-400   bg-blue-500/10   border-blue-500/20",PATCH:"text-yellow-400 bg-yellow-500/10 border-yellow-500/20",DELETE:"text-red-400    bg-red-500/10    border-red-500/20"};function fe(){const{tenant_id:h}=te(),n=i.useRef(!0),[l,m]=i.useState(0),[x,X]=i.useState([]),[W,I]=i.useState(!0),[j,D]=i.useState(!1),[g,$]=i.useState(""),[P,T]=i.useState(!1),[w,M]=i.useState({}),[_,C]=i.useState({}),[o,y]=i.useState(null),[O,N]=i.useState(""),[H,L]=i.useState(""),[R,K]=i.useState({}),[S,b]=i.useState({}),r=h||"00000000-0000-0000-0000-000000000001",u="a245cc68-19aa-48a7-8862-f3d7f0332ff6",J="a0c1849b-3b60-40aa-a7ef-35557a7ceef6",q="37533cba-54a2-475c-94e2-319c3dfdf69e",d="$TOKEN",f=()=>{I(!0),N(""),v.getApiKeys().then(t=>{n.current&&X((t==null?void 0:t.data)||t||[])}).catch(t=>{n.current&&N((t==null?void 0:t.message)||"Failed to load API keys.")}).finally(()=>{n.current&&I(!1)})};i.useEffect(()=>(n.current=!0,f(),()=>{n.current=!1}),[]);const G=async()=>{if(g.trim()){D(!0),L("");try{await v.createApiKey({name:g.trim()}),$(""),T(!1),f()}catch(t){n.current&&L((t==null?void 0:t.message)||"Failed to create key.")}finally{n.current&&D(!1)}}},Y=async()=>{if(o){C(t=>({...t,[o.id]:!0}));try{await v.revokeApiKey(o.id),f()}catch(t){n.current&&N((t==null?void 0:t.message)||"Revoke failed.")}finally{n.current&&C(t=>({...t,[o.id]:!1})),y(null)}}},F=async t=>{if(t!=null&&t.key){K(a=>({...a,[t.id]:!0})),b(a=>({...a,[t.id]:null}));try{const a=await fetch(`${s}/agents?limit=1`,{headers:{Authorization:`Bearer ${t.key}`},credentials:"include"}),c=a.ok,Z=c?"✓ Works":`✗ ${a.status} ${a.statusText||""}`.trim();n.current&&b(ee=>({...ee,[t.id]:{ok:c,label:Z}}))}catch(a){n.current&&b(c=>({...c,[t.id]:{ok:!1,label:`✗ ${(a==null?void 0:a.message)||"network error"}`}}))}finally{n.current&&K(a=>({...a,[t.id]:!1})),setTimeout(()=>{n.current&&b(a=>({...a,[t.id]:null}))},5e3)}}},U=[{title:"Step 1 — Login (get JWT)",desc:"Returns a 15-minute access token in `data.access_token`. The same value is set as the httpOnly `acp_token` cookie for browser SDKs.",code:`TOKEN=$(curl -s -X POST ${s}/auth/token \\
  -H "Content-Type: application/json" \\
  -H "X-Tenant-ID: ${r}" \\
  -d '{"email":"demo@aegisagent.in","password":"demo1234"}' \\
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['access_token'])")

echo "Token: \${TOKEN:0:40}..."`},{title:"Step 2 — List registered agents",desc:"All demo agents pre-seeded in this tenant. Use these IDs for the calls below.",code:`curl -s "${s}/agents?size=10" \\
  -H "Authorization: Bearer ${d}" \\
  -H "X-Tenant-ID: ${r}" \\
  | python3 -m json.tool`},{title:"Step 3 — Safe tool call → ALLOWED",desc:"demo-agent has `search_web` in its allow-list. Risk-scored, allowed.",code:`curl -s -X POST ${s}/execute \\
  -H "Authorization: Bearer ${d}" \\
  -H "X-Tenant-ID: ${r}" \\
  -H "Content-Type: application/json" \\
  -d '{"agent_id":"${u}","tool":"search_web","parameters":{"query":"AI governance"}}' \\
  | python3 -m json.tool

# Expected: {"action":"allow","risk":0.11,"signals":{...},"reasons":[...]}`},{title:"Step 4 — PII exfiltration attempt → BLOCKED (403)",desc:"Inference Proxy detects SSN/credit-card patterns in tool input and hard-denies before the tool runs.",code:`curl -s -X POST ${s}/execute \\
  -H "Authorization: Bearer ${d}" \\
  -H "X-Tenant-ID: ${r}" \\
  -H "Content-Type: application/json" \\
  -d '{"agent_id":"${u}","tool":"send_email","parameters":{"body":"Customer SSN is 123-45-6789, DOB 01/01/1985"}}'

# Expected: HTTP 403 {"error":"Security: PII or credential data detected ..."}`},{title:"Step 5 — RCE attempt → BLOCKED (403)",desc:"Dangerous code patterns (rm -rf, os.system, etc.) blocked by the RCE detector.",code:`curl -s -X POST ${s}/execute \\
  -H "Authorization: Bearer ${d}" \\
  -H "X-Tenant-ID: ${r}" \\
  -H "Content-Type: application/json" \\
  -d '{"agent_id":"${u}","tool":"run_code","parameters":{"exec":"os.system(\\"rm -rf /\\")"}}'

# Expected: HTTP 403 {"error":"Security: Dangerous code pattern detected ..."}`},{title:"Step 6 — SQL injection → BLOCKED (403)",desc:"db-copilot-demo has run_query allowed, but stacked statements / DROP TABLE / boolean blind injection are detected before execution.",code:`curl -s -X POST ${s}/execute \\
  -H "Authorization: Bearer ${d}" \\
  -H "X-Tenant-ID: ${r}" \\
  -H "Content-Type: application/json" \\
  -d '{"agent_id":"${J}","tool":"run_query","parameters":{"query":"SELECT * FROM users WHERE 1=1; DROP TABLE users; --"}}'

# Expected: HTTP 403 {"error":"Security: SQL injection detected ..."}`},{title:"Step 7 — Destructive k8s op → BLOCKED (403)",desc:"devops-agent-demo can run kubectl_get/delete, but the destructive-namespace detector blocks production-class targets and broad selectors.",code:`curl -s -X POST ${s}/execute \\
  -H "Authorization: Bearer ${d}" \\
  -H "X-Tenant-ID: ${r}" \\
  -H "Content-Type: application/json" \\
  -d '{"agent_id":"${q}","tool":"kubectl_delete","parameters":{"resource":"all","namespace":"production"}}'

# Expected: HTTP 403 {"error":"Security: destructive k8s op on production namespace"}`},{title:"Step 8 — Read the audit trail",desc:"Every allow + block has a SHA-256 hash chained to the previous row. Tamper-evident, signed with ed25519.",code:`curl -s "${s}/audit/logs?limit=5" \\
  -H "Authorization: Bearer ${d}" \\
  -H "X-Tenant-ID: ${r}" \\
  | python3 -c "import sys,json; d=json.load(sys.stdin)['data']; print('total:',d['total']); [print(f\\"  {i['decision']:8}{i['tool'] or '-':25} hash={i.get('event_hash','-')[:24]}...\\") for i in d['items']]"`},{title:"Step 9 — Verify chain integrity",desc:"Walks the entire chain server-side and reports any tampered or skipped rows.",code:`curl -s "${s}/audit/logs/verify" \\
  -H "Authorization: Bearer ${d}" \\
  -H "X-Tenant-ID: ${r}" \\
  | python3 -c "import sys,json; d=json.load(sys.stdin)['data']; print(f\\"chain valid: {d['valid']}  processed: {d['processed_count']}  violations: {d['error_count']}\\")"`},{title:"Step 10 — Stream live events (SSE)",desc:"Long-poll Server-Sent Events. Run this in one tab, then trigger /execute in another and watch the events arrive in real time.",code:`curl -N "${s}/events/stream?token=\${TOKEN}"

# Each line of output is:
#   event: connected         (initial handshake)
#   data: {...payload...}    (tool_executed, policy_decision, etc.)
#   event: heartbeat         (every 15s)`},{title:"Step 11 — System health",desc:"12 service status snapshot — used by the ALB target check.",code:`curl -s ${s}/system/health | python3 -m json.tool`},{title:"Step 12 — Risk summary",desc:"Tenant-wide block rate, high-risk agents, signal weights.",code:`curl -s ${s}/risk/summary \\
  -H "Authorization: Bearer ${d}" \\
  -H "X-Tenant-ID: ${r}" \\
  | python3 -m json.tool`}],V=`# pip install httpx
# Real working sample against ${s}
import asyncio, httpx, os

GATEWAY  = "${s}"
TENANT   = "${r}"
EMAIL    = "demo@aegisagent.in"
PASSWORD = "demo1234"
AGENT_ID = "${u}"  # demo-agent

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

asyncio.run(main())`,Q=`// Real working sample against ${s}
// Node 18+ has fetch built in.
const GATEWAY  = '${s}';
const TENANT   = '${r}';
const EMAIL    = 'demo@aegisagent.in';
const PASSWORD = 'demo1234';
const AGENT_ID = '${u}'; // demo-agent

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
})();`;return e.jsxs("div",{className:"space-y-6 animate-fade-in",children:[e.jsx("div",{className:"page-header",children:e.jsxs("div",{children:[e.jsx("h1",{className:"text-2xl font-bold text-white tracking-tight",children:"Developer Panel"}),e.jsx("p",{className:"text-xs text-neutral-500 mt-0.5",children:"API keys, integration examples, and endpoint reference"})]})}),e.jsx("div",{className:"flex gap-1 p-1 bg-[#080808] border border-white/5 rounded-xl w-fit",role:"tablist","aria-label":"Developer panel sections",children:ce.map((t,a)=>e.jsx("button",{type:"button",role:"tab","aria-selected":l===a,onClick:()=>m(a),className:`px-4 py-2 rounded-lg text-xs font-bold transition-colors ${l===a?"bg-white text-black":"text-neutral-500 hover:text-white"}`,children:t},a))}),l===0&&e.jsx(A,{title:"API Keys",icon:B,children:e.jsxs("div",{className:"space-y-4",children:[e.jsxs("div",{className:"flex items-center justify-between",children:[e.jsx("p",{className:"text-xs text-neutral-500",children:"API keys allow programmatic access to the gateway."}),e.jsxs(p,{size:"sm",onClick:()=>T(!P),children:[e.jsx(z,{size:13,"aria-hidden":"true"})," New Key"]})]}),P&&e.jsxs("div",{className:"flex items-center gap-3 p-4 rounded-xl bg-white/[0.02] border border-white/5",children:[e.jsx("input",{name:"input",type:"text",value:g,onChange:t=>$(t.target.value),onKeyDown:t=>t.key==="Enter"&&G(),placeholder:"Key name (e.g. prod-service)","aria-label":"New API key name",className:"input-standard flex-1 h-9 font-mono"}),e.jsx(p,{size:"sm",loading:j,disabled:j||!g.trim(),onClick:G,children:j?"Creating…":"Create"})]}),H&&e.jsx("div",{className:"mb-3 px-3 py-2 rounded-lg border border-red-500/30 bg-red-500/10 text-xs text-red-400",role:"alert",children:H}),O&&e.jsxs("div",{className:"mb-3 flex items-center justify-between gap-3 px-3 py-2 rounded-lg border border-red-500/30 bg-red-500/10",role:"alert",children:[e.jsx("span",{className:"text-xs text-red-400",children:O}),e.jsx("button",{onClick:f,className:"text-xs text-red-300 underline",children:"Retry"})]}),W?e.jsx(ae,{variant:"row",count:3}):x.length===0?e.jsxs("div",{className:"flex flex-col items-center justify-center py-10 px-4 text-center rounded-xl border border-dashed border-white/10 bg-white/[0.015]",children:[e.jsx(B,{size:28,className:"text-neutral-700 mb-3","aria-hidden":"true"}),e.jsx("p",{className:"text-sm font-medium text-neutral-200",children:"No API keys — create one to integrate"}),e.jsx("p",{className:"text-xs text-neutral-500 mt-1 max-w-sm",children:"API keys authenticate SDK calls (LangChain / OpenAI / Anthropic) and direct REST against the gateway."}),e.jsxs(p,{size:"sm",className:"mt-4",onClick:()=>T(!0),children:[e.jsx(z,{size:13,"aria-hidden":"true"})," Create API key"]})]}):e.jsx("div",{className:"space-y-2",children:x.map(t=>e.jsxs("div",{className:"flex items-center gap-4 p-3 rounded-xl bg-white/[0.02] border border-white/5",children:[e.jsxs("div",{className:"flex-1 min-w-0",children:[e.jsx("p",{className:"text-xs font-semibold text-white",children:t.name}),e.jsx("p",{className:"text-xs text-neutral-600 font-mono mt-0.5",children:w[t.id]?t.key||t.id:"••••••••••••••••••••••••••••••••"})]}),e.jsx("span",{className:`text-xs font-bold ${t.status==="active"?"text-green-400":"text-neutral-500"}`,children:t.status||"active"}),e.jsx("p",{className:"text-xs text-neutral-600 hidden sm:block",children:t.created_at?new Date(t.created_at).toLocaleDateString():"—"}),e.jsxs("div",{className:"flex items-center gap-2",children:[S[t.id]&&e.jsx("span",{className:`text-[11px] font-mono ${S[t.id].ok?"text-green-400":"text-red-400"}`,role:"status","aria-live":"polite",children:S[t.id].label}),t.key&&e.jsx(p,{size:"xs",variant:"ghost",loading:R[t.id],disabled:R[t.id],onClick:()=>F(t),"aria-label":`Test API key ${t.name}`,children:"Test"}),e.jsx("button",{type:"button",onClick:()=>M(a=>({...a,[t.id]:!a[t.id]})),"aria-label":w[t.id]?"Hide key":"Show key",className:"p-1.5 text-neutral-500 hover:text-white transition-colors",children:w[t.id]?e.jsx(ne,{size:13,"aria-hidden":"true"}):e.jsx(ie,{size:13,"aria-hidden":"true"})}),e.jsx(E,{text:t.key||t.id}),e.jsx("button",{type:"button",onClick:()=>y({id:t.id,name:t.name}),disabled:_[t.id],"aria-label":`Revoke API key ${t.name}`,className:"p-1.5 text-neutral-500 hover:text-red-400 transition-colors disabled:opacity-40",children:e.jsx(re,{size:13,"aria-hidden":"true"})})]})]},t.id))})]})}),l===1&&e.jsxs("div",{className:"space-y-5",children:[e.jsxs("div",{className:"rounded-xl border border-blue-500/20 bg-blue-500/[0.05] p-4 text-xs text-neutral-300",children:[e.jsx("p",{className:"font-semibold text-blue-300 mb-1",children:"Live tutorial against this deployment"}),e.jsxs("p",{className:"text-neutral-400",children:["Every snippet below runs against ",e.jsx("code",{className:"text-blue-300 font-mono",children:s})," with the demo tenant and seeded agent IDs. Start with Step 1 — it sets a ",e.jsx("code",{className:"text-blue-300 font-mono",children:"$TOKEN"})," env var the later steps reuse. Each block has a copy button; paste straight into a Mac / Linux / WSL terminal."]})]}),U.map((t,a)=>e.jsxs("div",{children:[e.jsx("p",{className:"text-xs font-bold text-neutral-400 mb-1 uppercase tracking-widest",children:t.title}),t.desc&&e.jsx("p",{className:"text-[11px] text-neutral-500 mb-2 leading-snug",children:t.desc}),e.jsx(k,{code:t.code,language:"bash"})]},a))]}),l===2&&e.jsxs("div",{className:"space-y-8",children:[e.jsxs("div",{children:[e.jsx("p",{className:"text-xs font-bold text-neutral-400 mb-3 uppercase tracking-widest",children:"Framework Integrations — 3-Line Install"}),e.jsx("div",{className:"grid grid-cols-1 gap-4",children:[{label:"LangChain",install:"pip install aegis-langchain==1.1.0",code:`from aegis_langchain import AegisMiddleware
agent = AegisMiddleware(my_langchain_agent, api_key="acp_...")
result = agent.invoke({"input": "analyze /etc/passwd"})  # automatically blocked`},{label:"OpenAI",install:"pip install aegis-openai==1.1.0",code:`from aegis_openai import AegisOpenAI
client = AegisOpenAI(aegis_key="acp_...", tenant_id="${r}")
response = client.chat.completions.create(model="gpt-4o", messages=[...], tools=[...])`},{label:"Anthropic / Claude",install:"pip install aegis-anthropic==1.1.0",code:`from aegis_anthropic import AegisAnthropic
client = AegisAnthropic(aegis_key="acp_...", tenant_id="${r}")
response = client.messages.create(model="claude-opus-4-7", max_tokens=1024, tools=[...], messages=[...])`}].map(({label:t,install:a,code:c})=>e.jsxs("div",{className:"rounded-xl border border-white/5 bg-white/[0.01] overflow-hidden",children:[e.jsxs("div",{className:"flex items-center justify-between px-4 py-2 border-b border-white/5 bg-white/[0.02]",children:[e.jsx("span",{className:"text-xs font-bold text-white",children:t}),e.jsx(E,{text:a})]}),e.jsx("div",{className:"px-4 py-2 border-b border-white/5 bg-black/30",children:e.jsx("code",{className:"text-[11px] font-mono text-amber-400",children:a})}),e.jsx("pre",{className:"px-4 py-3 text-[11px] font-mono text-green-400 overflow-x-auto whitespace-pre leading-relaxed",children:c})]},t))}),e.jsxs("p",{className:"mt-2 text-[10px] text-neutral-600",children:["All three packages wrap the ",e.jsx("code",{children:"/execute"})," endpoint. Blocked tool calls return a descriptive message instead of executing — your agent handles it naturally."]})]}),e.jsxs("div",{children:[e.jsx("p",{className:"text-xs font-bold text-neutral-400 mb-2 uppercase tracking-widest",children:"Low-Level Python SDK"}),e.jsx("div",{className:"p-3 rounded-xl bg-blue-500/5 border border-blue-500/10 text-xs text-blue-300 mb-3",children:"The ACP SDK wraps the gateway API with automatic authentication, idempotency keys, and typed error handling."}),e.jsx(k,{code:V,language:"python"})]}),e.jsxs("div",{children:[e.jsx("p",{className:"text-xs font-bold text-neutral-400 mb-2 uppercase tracking-widest",children:"JavaScript SDK"}),e.jsx(k,{code:Q,language:"javascript"})]}),e.jsx(A,{title:"Required Headers",children:e.jsx("div",{className:"space-y-0 text-xs font-mono",children:[["Authorization","Bearer <JWT or acp_key>","JWT from /auth/token or API key created above"],["X-Tenant-ID","<uuid>","Tenant isolation — must match JWT claim"],["X-Agent-ID",'<agent-uuid or "dashboard-agent">',"Agent context for policy evaluation"],["X-Request-ID","<uuid>","Distributed tracing (auto-generated by SDK)"],["Content-Type","application/json","Required for POST/PATCH requests"]].map(([t,a,c])=>e.jsxs("div",{className:"flex gap-4 py-2.5 border-b border-white/[0.04] last:border-0",children:[e.jsx("span",{className:"text-white w-32 shrink-0",children:t}),e.jsx("span",{className:"text-green-400 flex-1",children:a}),e.jsx("span",{className:"text-neutral-600 hidden md:block",children:c})]},t))})})]}),l===3&&e.jsx(A,{title:"API Endpoint Reference",icon:oe,children:e.jsx("div",{className:"table-scroll",children:e.jsxs("table",{className:"table-base min-w-[600px]",role:"table",children:[e.jsx("thead",{children:e.jsx("tr",{children:["Method","Path","Auth","Description"].map(t=>e.jsx("th",{className:"table-th first:pl-5",children:t},t))})}),e.jsx("tbody",{children:he.map((t,a)=>e.jsxs("tr",{className:"table-row",children:[e.jsx("td",{className:"table-td first:pl-5",children:e.jsx("span",{className:`status-badge ${pe[t.method]??"text-neutral-400 bg-white/5 border-white/10"}`,children:t.method})}),e.jsx("td",{className:"table-td font-mono text-white",children:t.path}),e.jsx("td",{className:"table-td",children:e.jsx("span",{className:`text-xs font-bold ${t.auth?"text-yellow-400":"text-green-400"}`,children:t.auth?"JWT":"None"})}),e.jsx("td",{className:"table-td text-neutral-400",children:t.description})]},a))})]})})}),e.jsxs(se,{isOpen:!!o,title:"Revoke API Key",onClose:()=>y(null),footer:e.jsxs(e.Fragment,{children:[e.jsx(p,{variant:"ghost",size:"sm",onClick:()=>y(null),children:"Cancel"}),e.jsx(p,{variant:"danger",size:"sm",loading:_[o==null?void 0:o.id],onClick:Y,children:"Revoke Key"})]}),children:[e.jsxs("p",{className:"text-sm text-neutral-300",children:["Revoke API key ",e.jsxs("span",{className:"font-bold text-white",children:['"',o==null?void 0:o.name,'"']}),"?"]}),e.jsx("p",{className:"text-xs text-neutral-500 mt-2",children:"This cannot be undone. Any service using this key will lose access immediately."})]})]})}export{fe as default};
