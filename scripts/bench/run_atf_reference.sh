#!/usr/bin/env bash
# ATF v3.2 §12.1 — reference workload runner.
#
# Runs the atf_reference_workload locust harness against $ACP_TARGET
# with the parameters §12.1 specifies. Output CSVs land in
# reports/bench/atf_ref_<timestamp>/ so measurements are archivable
# per workload_id + date.
#
# One-shot invocation:
#   ACP_TOKEN=... ACP_TARGET=https://gate.dev.aegisagent.in \\
#     ./scripts/bench/run_atf_reference.sh
#
# Publish the resulting p50/p99 + throughput into §12.2 (replacing
# *TBD* cells) with the workload_id inline.

set -eu

: "${ACP_TARGET:?ACP_TARGET must be set (e.g. https://gate.dev.aegisagent.in)}"
: "${ACP_TOKEN:?ACP_TOKEN must be set (test-tenant JWT with agent role)}"

STAMP="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="reports/bench/atf_ref_${STAMP}"
mkdir -p "${OUT_DIR}"

# §12.1: 20 concurrent steady, burst to 50. --users controls steady;
# --spawn-rate ramps at 5/s so we see the burst behavior in the first minute.
USERS="${ATF_USERS:-20}"
SPAWN_RATE="${ATF_SPAWN_RATE:-5}"
RUN_TIME="${ATF_RUN_TIME:-30m}"

exec .venv/bin/locust \
  -f tests/load/atf_reference_workload.py \
  --host "${ACP_TARGET}" \
  --users "${USERS}" \
  --spawn-rate "${SPAWN_RATE}" \
  --run-time "${RUN_TIME}" \
  --headless \
  --csv "${OUT_DIR}/atf_ref" \
  --html "${OUT_DIR}/atf_ref.html" \
  --logfile "${OUT_DIR}/locust.log"
