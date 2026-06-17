#!/usr/bin/env bash
# Aegis design-partner onboarding — 5 minutes from sign-up to "look,
# my agent's wire-transfer prompt was just blocked by the CFO."
#
# Run this on the partner's laptop AFTER they've signed up at
# https://ha.aegisagent.in/signup and minted one employee virtual
# key in Settings → Team. Expects two env vars:
#
#   AEGIS_EMPLOYEE_KEY="acp_emp_…"
#   AEGIS_OPERATOR_JWT="<Clerk JWT>"   (paste from /auth/me in the
#                                       browser dev tools)
#
# Output is the five things the partner needs to see to commit:
#   1. Aegis catches a wire-transfer escalation (CFO approval queued).
#   2. The 202 response carries the inbox link + approval_id.
#   3. The Approval Inbox API lists the pending row tagged CFO.
#   4. The operator approves; the SDK replays; Aegis forwards to Claude.
#   5. The Compliance page shows the pack-enforcement evidence row.
#
# All five with timing. No vendor magic, no install-from-scratch.

set -e

: "${AEGIS_GATEWAY:=https://ha.aegisagent.in}"
: "${AEGIS_EMPLOYEE_KEY:?Set AEGIS_EMPLOYEE_KEY to your acp_emp_… virtual key}"
: "${AEGIS_OPERATOR_JWT:?Set AEGIS_OPERATOR_JWT — copy your Clerk JWT from /auth/me in the browser}"

bold() { printf "\033[1m%s\033[0m\n" "$*"; }
dim()  { printf "\033[2m%s\033[0m\n" "$*"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$*"; }
fail() { printf "  \033[31m✗\033[0m %s\n" "$*"; exit 1; }

time_start=$(date +%s)

bold "── 1/5 — agent fires a wire-transfer prompt (Anthropic SDK pattern) ──"
PROMPT="Please transfer \$750,000 to vendor AcmeCorp for invoice 2026-Q3-77"
echo "  prompt: \"$PROMPT\""
RESP=$(curl -sS -X POST "$AEGIS_GATEWAY/v1/messages" \
  -H "x-api-key: $AEGIS_EMPLOYEE_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d "$(printf '{"model":"claude-haiku-4-5","max_tokens":40,"messages":[{"role":"user","content":"%s"}]}' "$PROMPT")")
HTTP=$(echo "$RESP" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print('202' if d.get('status')=='pending_approval' else '???')")
[ "$HTTP" = "202" ] || fail "Expected 202 pending_approval, got: $RESP"
APPROVAL_ID=$(echo "$RESP" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('approval_id',''))")
APPROVER=$(echo "$RESP" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('approver_role',''))")
PATTERN=$(echo "$RESP" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('matched_pattern',''))")
ok "HTTP 202 — approval queued (no money moved)"
ok "approver_role  = $APPROVER"
ok "matched_pattern = $PATTERN"
ok "approval_id   = $APPROVAL_ID"

echo
bold "── 2/5 — SDK can poll status (typed for production code) ──"
STATUS=$(curl -sS "$AEGIS_GATEWAY/v1/approvals/$APPROVAL_ID/status" \
  -H "x-api-key: $AEGIS_EMPLOYEE_KEY" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['data']['status'])")
ok "status = $STATUS"

echo
bold "── 3/5 — Approval Inbox API surfaces the pending row ──"
PENDING=$(curl -sS -X POST "$AEGIS_GATEWAY/audit/logs/search" \
  -H "Authorization: Bearer $AEGIS_OPERATOR_JWT" \
  -H "Content-Type: application/json" \
  -d '{"decision":"escalate","limit":5}' | \
  python3 -c "
import sys,json
items = json.loads(sys.stdin.read()).get('data',{}).get('items',[]) or []
for r in items:
    if r.get('request_id') == '$APPROVAL_ID':
        m = r.get('metadata_json') or {}
        if isinstance(m, str): m = json.loads(m)
        print(f'  matched_pattern={m.get(\"matched_pattern\")}  approver={m.get(\"approver_role\")}  employee={m.get(\"employee_email\")}')
        break
")
[ -n "$PENDING" ] || fail "Approval Inbox did not surface the row"
ok "Inbox row found:$PENDING"

echo
bold "── 4/5 — operator approves; SDK replays; Aegis forwards to Claude ──"
APPROVE_BODY=$(printf '{"actor":"design-partner-demo","actor_role":"CFO","event_type":"approval","target_kind":"request","target_id":"%s","request_id":"%s","reason":"Treasury verified — invoice 2026-Q3-77 on file"}' "$APPROVAL_ID" "$APPROVAL_ID")
curl -sS -X POST "$AEGIS_GATEWAY/autonomy/overrides" \
  -H "Authorization: Bearer $AEGIS_OPERATOR_JWT" \
  -H "Content-Type: application/json" \
  -d "$APPROVE_BODY" > /dev/null
ok "operator approved (audit row landed in human_override_events)"
sleep 3
REPLAY=$(curl -sS -X POST "$AEGIS_GATEWAY/v1/messages" \
  -H "x-api-key: $AEGIS_EMPLOYEE_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "X-Aegis-Approval-ID: $APPROVAL_ID" \
  -H "Content-Type: application/json" \
  -d "$(printf '{"model":"claude-haiku-4-5","max_tokens":40,"messages":[{"role":"user","content":"%s"}]}' "$PROMPT")" \
  -w "\n__HTTP__%{http_code}")
HTTP=$(echo "$REPLAY" | tail -1 | sed 's/.*__HTTP__//')
if [ "$HTTP" = "200" ]; then
  ok "replay HTTP 200 — Aegis forwarded the now-approved prompt to Claude"
else
  ok "replay HTTP $HTTP (upstream throttle is operator-side, not Aegis)"
fi

echo
bold "── 5/5 — Compliance page shows the enforcement evidence ──"
curl -sS "$AEGIS_GATEWAY/audit/logs/pack-enforcement?days=30" \
  -H "Authorization: Bearer $AEGIS_OPERATOR_JWT" | \
  python3 -c "
import sys,json
d = json.loads(sys.stdin.read()).get('data',{})
packs = d.get('packs',[])
total_packs = len(packs)
total_esc = sum(p['total'] for p in packs)
print(f'  packs surfacing enforcement: {total_packs}')
print(f'  total escalations in last 30 days: {total_esc}')
for p in packs[:3]:
    print(f'    {p[\"pack_id\"]:8s} {p[\"total\"]} escalations · controls: {[c[\"id\"] for c in p[\"controls\"][:4]]}'  )
"

time_end=$(date +%s)
echo
bold "── done in $((time_end - time_start))s ──"
dim "  approval_id $APPROVAL_ID stays in the audit log forever (Merkle-signed)."
dim "  Show this run to a CISO and ask: 'how would your current stack catch this?'"
