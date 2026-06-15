#!/bin/bash
# R2 — 2-minute auditor flow.
#
# The exact thing an EU AI Act auditor does, end-to-end, on their own
# laptop. No CLI access to Aegis. No trusted dashboard. Just two curl
# calls and one Python verifier.
#
# Run:  bash scripts/demo/auditor_flow.sh
# Env:  AEGIS_URL          (default: https://ha.aegisagent.in)
#       AEGIS_TENANT_ID    (default: the demo tenant)
#       AEGIS_EMAIL        (default: admin@acp.local)
#       AEGIS_PASSWORD     (default: admin1234)
#       AEGIS_PERIOD_START (default: 90 days ago)
#       AEGIS_PERIOD_END   (default: now)

set -o pipefail

URL="${AEGIS_URL:-https://ha.aegisagent.in}"
TENANT="${AEGIS_TENANT_ID:-00000000-0000-0000-0000-000000000001}"
EMAIL="${AEGIS_EMAIL:-admin@acp.local}"
PASS="${AEGIS_PASSWORD:-admin1234}"
PERIOD_START="${AEGIS_PERIOD_START:-$(date -u -d '90 days ago' +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -v-90d +%Y-%m-%dT%H:%M:%S)}"
PERIOD_END="${AEGIS_PERIOD_END:-$(date -u +%Y-%m-%dT%H:%M:%S)}"

echo "=== R2 — Auditor flow on ${URL} ==="
echo "  tenant:        ${TENANT}"
echo "  period start:  ${PERIOD_START}"
echo "  period end:    ${PERIOD_END}"
echo

# ── Step 1 — log in (an enterprise auditor would get a long-lived API key) ──
echo "[1/4] Login & get token..."
TOKEN=$(curl -ks -X POST "${URL}/auth/token" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: ${TENANT}" \
  -d "{\"email\":\"${EMAIL}\",\"password\":\"${PASS}\"}" \
  | python3 -c "import sys,json;print(json.load(sys.stdin).get('data',{}).get('access_token',''))")

if [[ -z "$TOKEN" ]]; then
    echo "ERROR: could not authenticate to ${URL}/auth/token"
    exit 1
fi
echo "       token length: ${#TOKEN}"

# ── Step 2 — pull the public signing key (the only Aegis-side trust anchor) ──
# Even this can be skipped if the auditor archived a prior key — the
# bundle itself embeds the key, so this step is only for cross-checking
# that the bundle isn't lying about the active key.
echo
echo "[2/4] Pulling /receipts/key — the auditor's trust anchor..."
curl -ks "${URL}/receipts/key" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "X-Tenant-ID: ${TENANT}" \
  -o /tmp/aegis-public-key.json
fingerprint=$(python3 -c "import json;print(json.load(open('/tmp/aegis-public-key.json')).get('fingerprint',''))")
echo "       active key fingerprint: ${fingerprint}"

# ── Step 3 — download the self-contained evidence bundle ────────────────────
echo
echo "[3/4] Downloading verifiable evidence bundle (eu-ai-act)..."
BUNDLE=/tmp/aegis-evidence-bundle.json
curl -ks "${URL}/compliance/verifiable-bundle/eu-ai-act?period_start=${PERIOD_START}&period_end=${PERIOD_END}" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "X-Tenant-ID: ${TENANT}" \
  -o "${BUNDLE}"

bundle_size=$(wc -c < "${BUNDLE}")
echo "       bundle size: ${bundle_size} bytes"

python3 -c "
import json
b = json.load(open('${BUNDLE}'))
print(f'       format:    {b.get(\"format_version\")}')
print(f'       records:   {len(b.get(\"records\", []))}')
print(f'       keys:      {len(b.get(\"public_keys\", []))}')
print(f'       roots:     {len(b.get(\"merkle_roots\", []))}')
print(f'       retention: {b.get(\"retention_metadata\", {}).get(\"configured_retention_days\")} days')
print(f'       earliest:  {b.get(\"retention_metadata\", {}).get(\"earliest_row_in_bundle\")}')
print(f'       latest:    {b.get(\"retention_metadata\", {}).get(\"latest_row_in_bundle\")}')
"

# ── Step 4 — verify offline. No Aegis network call from here on. ────────────
echo
echo "[4/4] Verifying offline with aegis-verify..."
if ! python3 -c "import cryptography" 2>/dev/null; then
    echo "  pip install cryptography (one-time)"
    pip install --quiet cryptography
fi

PYTHONPATH=tools python3 -m aegis_verify --bundle "${BUNDLE}" --verbose
exit_code=$?

echo
if [[ $exit_code -eq 0 ]]; then
    echo "═══════════════════════════════════════════════════════════════════"
    echo "  AUDIT VERIFIED — every signature, hash chain, and Merkle root"
    echo "  in this bundle was validated WITHOUT trusting Aegis. The auditor"
    echo "  can sign off on this evidence on the regulator's behalf."
    echo "═══════════════════════════════════════════════════════════════════"
else
    echo "═══════════════════════════════════════════════════════════════════"
    echo "  VERIFICATION FAILED — see report above for the broken row."
    echo "═══════════════════════════════════════════════════════════════════"
fi
exit $exit_code
