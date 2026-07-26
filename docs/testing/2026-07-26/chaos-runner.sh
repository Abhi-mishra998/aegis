#!/usr/bin/env bash
# Chaos runner — executes destructive tests against ONE host + measures behavior.
# Runs on the target EC2 instance (not from laptop) so we can kill containers
# and read the local docker events in real time.
set -uo pipefail

log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"; }

VICTIM=${1:-acp_redis}          # container to kill (only exists locally though — Redis is ElastiCache)
INSPECT_TARGET=${2:-gateway}    # container to inspect for degradation

# Helper — measure /execute latency from inside gateway (past WAF) N times
sample_latency() {
    local n=${1:-5}
    local total=0
    local ok=0
    for i in $(seq 1 $n); do
        local start=$(date +%s%N)
        code=$(docker exec acp_gateway curl -sSk -o /dev/null -w '%{http_code}' \
            -H "x-api-key: acp_emp_REDACTED_test_key_replace_with_your_own" \
            -H "X-Tenant-ID: 462d6e58-559f-44f3-8b0f-185aa9235909" \
            -H "X-Agent-ID: b64b52da-93f0-44f3-add2-7d78b19f39b0" \
            -H "Content-Type: application/json" \
            -d '{"agent_id":"b64b52da-93f0-44f3-add2-7d78b19f39b0","tool":"search_web","parameters":{"q":"chaos"}}' \
            http://gateway:8000/execute 2>/dev/null)
        local end=$(date +%s%N)
        local ms=$(( (end - start) / 1000000 ))
        total=$((total + ms))
        [ "$code" = "200" ] && ok=$((ok + 1))
        printf "  %-3s → %s (%dms)\n" "$i" "$code" "$ms"
    done
    local avg=$((total / n))
    echo "  summary: $ok/$n allowed, avg=${avg}ms"
}

case "${MODE:-help}" in
    kill-decision)
        log "=== CHAOS: kill decision service ==="
        log "baseline (before kill):"
        sample_latency 3
        log "killing acp_decision..."
        docker kill acp_decision
        log "sample during outage:"
        sample_latency 5
        log "restarting..."
        docker start acp_decision
        log "waiting for decision healthy..."
        until docker inspect --format '{{.State.Health.Status}}' acp_decision 2>/dev/null | grep -q healthy; do sleep 2; done
        log "sample after recovery:"
        sample_latency 3
        ;;
    kill-audit)
        log "=== CHAOS: kill audit service ==="
        log "baseline:"
        sample_latency 3
        log "killing acp_audit..."
        docker kill acp_audit
        log "sample during outage:"
        sample_latency 5
        log "restarting..."
        docker start acp_audit
        until docker inspect --format '{{.State.Health.Status}}' acp_audit 2>/dev/null | grep -q healthy; do sleep 2; done
        log "sample after recovery:"
        sample_latency 3
        ;;
    kill-opa)
        log "=== CHAOS: kill OPA ==="
        log "baseline:"
        sample_latency 3
        log "killing acp_opa..."
        docker kill acp_opa
        log "sample during outage (fail-closed expected):"
        sample_latency 5
        log "restarting..."
        docker start acp_opa
        until docker inspect --format '{{.State.Health.Status}}' acp_opa 2>/dev/null | grep -q healthy; do sleep 2; done
        log "sample after recovery:"
        sample_latency 3
        ;;
    kill-gateway)
        log "=== CHAOS: restart gateway (rolling) ==="
        log "baseline via /status from outside gateway:"
        curl -sS -H "User-Agent: Mozilla/5.0" https://aegisagent.in/status -o /dev/null -w 'status=%{http_code} time=%{time_total}s\n'
        log "restarting local gateway ONLY (other host still up):"
        docker restart acp_gateway
        log "sample from other host via ALB during rolling restart:"
        for i in 1 2 3 4 5; do
            curl -sS -H "User-Agent: Mozilla/5.0" https://aegisagent.in/status -o /dev/null -w "  $i: %{http_code} %{time_total}s\n"
        done
        log "waiting for local gateway healthy:"
        until docker inspect --format '{{.State.Health.Status}}' acp_gateway 2>/dev/null | grep -q healthy; do sleep 2; done
        log "recovered"
        ;;
    *)
        echo "Usage: MODE={kill-decision|kill-audit|kill-opa|kill-gateway} $0"
        exit 1
        ;;
esac
