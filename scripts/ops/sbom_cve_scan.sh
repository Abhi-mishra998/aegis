#!/usr/bin/env bash
# Sprint EI-13 (2026-06-20). Run Trivy against a CycloneDX SBOM and emit
# a stable JSON of CVE findings that the diff step can consume.
#
# Why this script vs running Trivy inline in the workflow:
#   - The same logic runs in CI AND locally (operator sanity check before
#     a release; investor TDD spot-check).
#   - Stable output format means the diff step at sbom_cve_diff.py
#     doesn't have to chase Trivy CLI flag drift.
#   - Trivy alone exits non-zero on findings-present, which would make
#     the nightly job fail every night on chronic-unfixed CVEs.
#     This wrapper exits 0 on findings; the diff step decides what's NEW.
#
# Usage:
#   bash scripts/ops/sbom_cve_scan.sh <sbom.json> [<out.json>]
#
# Env:
#   SEVERITY    comma-list, default "HIGH,CRITICAL"
#   TRIVY_BIN   default 'trivy' on $PATH
#
# Exit codes:
#   0  scan succeeded (regardless of findings count)
#   1  bad args
#   2  trivy missing or crashed

set -uo pipefail

SBOM="${1:-}"
OUT="${2:-/tmp/sbom-cve-findings.json}"
SEVERITY="${SEVERITY:-HIGH,CRITICAL}"
TRIVY_BIN="${TRIVY_BIN:-trivy}"

if [ -z "$SBOM" ] || [ ! -f "$SBOM" ]; then
    echo "FAIL — usage: $0 <sbom.json> [<out.json>]" >&2
    [ -n "$SBOM" ] && [ ! -f "$SBOM" ] && echo "        SBOM not found: $SBOM" >&2
    exit 1
fi

if ! command -v "$TRIVY_BIN" >/dev/null 2>&1; then
    echo "FAIL — $TRIVY_BIN not on \$PATH." >&2
    echo "       macOS:  brew install trivy" >&2
    echo "       Linux:  curl -fsSL https://aquasecurity.github.io/trivy-repo/deb/public.key | sudo apt-key add -" >&2
    echo "               see https://aquasecurity.github.io/trivy/latest/getting-started/installation/" >&2
    exit 2
fi

echo "[sbom-cve] scanning ${SBOM} (severity=${SEVERITY})"

# Trivy emits a JSON report with Results[].Vulnerabilities[] entries.
# We capture stderr separately so a scan failure is distinguishable
# from "findings present" (different exit semantics).
TMP_RAW="$(mktemp)"
TMP_ERR="$(mktemp)"
trap 'rm -f "$TMP_RAW" "$TMP_ERR"' EXIT

if ! "$TRIVY_BIN" sbom \
        --severity "$SEVERITY" \
        --format json \
        --quiet \
        "$SBOM" > "$TMP_RAW" 2> "$TMP_ERR"; then
    # Trivy returns non-zero when --exit-code=1 is set OR on crash. We
    # don't pass --exit-code so non-zero here means crash.
    echo "FAIL — trivy scan crashed:" >&2
    cat "$TMP_ERR" >&2
    exit 2
fi

# Re-shape to the stable format the diff step expects. Each entry is one
# CVE × one affected package: {id, severity, package, installed_version,
# fixed_version, primary_url}. Sorted deterministically so the JSON is
# diff-friendly outside the diff script too (e.g. a human reading two
# nightly snapshots).
jq '
  [
    (.Results // [])[]
    | (.Vulnerabilities // [])[]
    | {
        id:                .VulnerabilityID,
        severity:          .Severity,
        package:           .PkgName,
        installed_version: .InstalledVersion,
        fixed_version:     (.FixedVersion // null),
        primary_url:       .PrimaryURL,
      }
  ]
  | sort_by(.id, .package, .installed_version)
' "$TMP_RAW" > "$OUT"

n="$(jq 'length' "$OUT")"
echo "[sbom-cve] wrote $n finding(s) to $OUT"
exit 0
