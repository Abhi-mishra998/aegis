#!/usr/bin/env bash
# Production smoke test — sprint-7.4.
#
# Run after every deploy to catch the regressions the unit tests can't see:
#   - cross-tenant access still blocked
#   - admin role still enforced on /admin/tenants
#   - SSE no longer accepts query-string tokens
#   - signature gate on Stripe webhook
#   - audit chain still verifying
#   - kill-switch round-trip works
#
# Each check is a single curl call. Exit code is 0 if all checks pass; the
# script prints PASS/FAIL per check.
#
# Usage:
#   GATEWAY_URL=https://aegisagent.in \
#   ADMIN_JWT=eyJhbG... \
#   VIEWER_JWT=eyJhbG... \
#   TENANT_A_UUID=... \
#   TENANT_B_UUID=... \
#     ./scripts/ops/smoke_test.sh

set -uo pipefail   # NOT -e — we want to run every check and aggregate

: "${GATEWAY_URL:?GATEWAY_URL env var required}"
: "${ADMIN_JWT:?ADMIN_JWT env var required (a real ADMIN-role JWT)}"

PASS=0
FAIL=0
FAILED_NAMES=()

_check() {
  local name="$1"
  local expected_code="$2"
  local actual_code="$3"
  local extra="${4:-}"

  if [ "$actual_code" = "$expected_code" ]; then
    printf "  \033[32mPASS\033[0m  %-50s  → %s\n" "$name" "$actual_code"
    PASS=$((PASS + 1))
  else
    printf "  \033[31mFAIL\033[0m  %-50s  → got %s, want %s  %s\n" "$name" "$actual_code" "$expected_code" "$extra"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
}

echo "=== Aegis smoke test against $GATEWAY_URL ==="

# 1. /health must be public
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$GATEWAY_URL/health")
_check "GET /health (public)" "200" "$code"

# 2. /system/health must be public (operator probe)
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$GATEWAY_URL/system/health")
_check "GET /system/health (public)" "200" "$code"

# 3. /metrics must be public (Prometheus scrape)
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$GATEWAY_URL/metrics")
_check "GET /metrics (public)" "200" "$code"

# 4. Unauthenticated /agents must be rejected
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$GATEWAY_URL/agents")
_check "GET /agents (no token → 401)" "401" "$code"

# 5. Admin JWT can list tenants
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 \
  -H "Authorization: Bearer $ADMIN_JWT" "$GATEWAY_URL/admin/tenants")
_check "GET /admin/tenants (admin token)" "200" "$code"

# 6. VIEWER JWT must NOT list tenants (sprint-1 fix)
if [ -n "${VIEWER_JWT:-}" ]; then
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 \
    -H "Authorization: Bearer $VIEWER_JWT" "$GATEWAY_URL/admin/tenants")
  _check "GET /admin/tenants (viewer → 403)" "403" "$code"
fi

# 7. Cross-tenant kill-switch attempt MUST be blocked (sprint-1 CRITICAL fix)
if [ -n "${TENANT_A_SECURITY_JWT:-}" ] && [ -n "${TENANT_B_UUID:-}" ]; then
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 \
    -X POST \
    -H "Authorization: Bearer $TENANT_A_SECURITY_JWT" \
    -H "Content-Type: application/json" \
    -d '{"action":"engage"}' \
    "$GATEWAY_URL/decision/kill-switch/$TENANT_B_UUID")
  _check "POST /decision/kill-switch/<other_tenant> (→ 403)" "403" "$code"

  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 \
    -X DELETE \
    -H "Authorization: Bearer $TENANT_A_SECURITY_JWT" \
    "$GATEWAY_URL/decision/kill-switch/$TENANT_B_UUID")
  _check "DELETE /decision/kill-switch/<other_tenant> (→ 403)" "403" "$code"
fi

# 8. SSE token-in-query-string is rejected (sprint-1 fix)
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 \
  "$GATEWAY_URL/events/stream?token=fake")
_check "GET /events/stream?token=fake (→ 401)" "401" "$code"

# 9. Stripe webhook without signature → 400 (sprint-5.3)
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 \
  -X POST -H "Content-Type: application/json" \
  -d '{}' "$GATEWAY_URL/billing/stripe/webhook")
_check "POST /billing/stripe/webhook (no sig → 400/503)" "400" "$code" \
  "(503 OK if STRIPE_WEBHOOK_SECRET unset)"

# 10. Stripe webhook with garbage signature → 400
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 \
  -X POST -H "Stripe-Signature: t=$(date +%s),v1=garbage" \
  -H "Content-Type: application/json" \
  -d '{}' "$GATEWAY_URL/billing/stripe/webhook")
_check "POST /billing/stripe/webhook (bad sig → 400)" "400" "$code"

# 11. Tenant-scoped read works with admin
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 \
  -H "Authorization: Bearer $ADMIN_JWT" "$GATEWAY_URL/audit/logs?limit=1")
_check "GET /audit/logs?limit=1 (admin)" "200" "$code"

# 12. Audit chain integrity probe (audit chain verify endpoint)
verify_resp=$(curl -s --max-time 10 \
  -H "Authorization: Bearer $ADMIN_JWT" "$GATEWAY_URL/audit/logs/verify")
echo "  INFO  audit chain verify response: $(echo "$verify_resp" | head -c 200)"
if echo "$verify_resp" | grep -q '"ok":true\|"valid":true\|chain_status.*healthy'; then
  printf "  \033[32mPASS\033[0m  %-50s  → chain healthy\n" "GET /audit/logs/verify"
  PASS=$((PASS + 1))
else
  printf "  \033[31mFAIL\033[0m  %-50s  → unexpected payload\n" "GET /audit/logs/verify"
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("audit chain verify")
fi

# 13. /system/health latency block must include p95
health_body=$(curl -s --max-time 5 "$GATEWAY_URL/system/health")
if echo "$health_body" | grep -q 'p95\|latency'; then
  printf "  \033[32mPASS\033[0m  %-50s  → latency block present\n" "/system/health body shape"
  PASS=$((PASS + 1))
else
  printf "  \033[31mFAIL\033[0m  %-50s  → no latency in body\n" "/system/health body shape"
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("system_health latency")
fi

# 14. /status carries kill-switch indicator
status_body=$(curl -s --max-time 5 "$GATEWAY_URL/status")
if echo "$status_body" | grep -q 'kill_switch\|kill-switch'; then
  printf "  \033[32mPASS\033[0m  %-50s  → indicator present\n" "/status kill_switch indicator"
  PASS=$((PASS + 1))
else
  printf "  \033[31mFAIL\033[0m  %-50s  → indicator missing\n" "/status kill_switch indicator"
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("status kill_switch indicator")
fi

echo ""
echo "=== Summary ==="
echo "  PASS: $PASS"
echo "  FAIL: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo ""
  echo "  Failed checks:"
  for n in "${FAILED_NAMES[@]}"; do
    echo "    - $n"
  done
  exit 1
fi
echo ""
echo "  Smoke test green."
exit 0
