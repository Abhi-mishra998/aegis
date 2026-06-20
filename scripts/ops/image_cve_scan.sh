#!/usr/bin/env bash
# Sprint EI-15 (2026-06-20). Run trivy against a list of SHA256-pinned
# container images. Emits findings in the SAME stable schema as
# sbom_cve_scan.sh so sbom_cve_diff.py can consume the union without
# any change.
#
# Why this exists vs the EI-13 SBOM scan: the Python-SBOM scan covers
# our application deps, but it does NOT cover the OS-layer of the
# base image (python:3.11-slim's Debian packages, postgres-alpine,
# nginx-alpine, etc). CVE-2025-WHATEVER in a Debian libcurl will not
# show up in the Python SBOM but WILL be in the running container.
# This script closes that gap.
#
# Inputs:
#   $1  file with one image ref per line (e.g. from list_pinned_images.sh)
#   $2  output JSON (default: /tmp/image-cve-findings.json)
#
# Env:
#   SEVERITY   default "HIGH,CRITICAL"
#   TRIVY_BIN  default 'trivy' on $PATH
#
# Each finding carries a `source` field with the format
# ``image:<ref-truncated-to-60-chars>`` so the rendered markdown
# summary can tell the operator WHERE the CVE came from.

set -uo pipefail

IN="${1:-}"
OUT="${2:-/tmp/image-cve-findings.json}"
SEVERITY="${SEVERITY:-HIGH,CRITICAL}"
TRIVY_BIN="${TRIVY_BIN:-trivy}"

if [ -z "$IN" ] || [ ! -f "$IN" ]; then
    echo "FAIL — usage: $0 <image-list.txt> [<out.json>]" >&2
    exit 1
fi

if ! command -v "$TRIVY_BIN" >/dev/null 2>&1; then
    echo "FAIL — $TRIVY_BIN not on \$PATH." >&2
    exit 2
fi

ALL_FINDINGS="[]"
TMP_RAW="$(mktemp)"
trap 'rm -f "$TMP_RAW"' EXIT

while IFS= read -r ref || [ -n "$ref" ]; do
    ref="$(echo "$ref" | sed -E 's|#.*$||' | xargs)"
    [ -z "$ref" ] && continue
    short="$(printf '%s' "$ref" | cut -c 1-60)"
    echo "[image-cve] scanning ${ref}"
    if ! "$TRIVY_BIN" image \
            --severity "$SEVERITY" \
            --format json \
            --quiet \
            "$ref" > "$TMP_RAW" 2>/dev/null; then
        # An image-pull failure or trivy crash on ONE image must not
        # take down the whole nightly. Log + continue + record a
        # placeholder finding so the diff can flag "we lost visibility".
        echo "[image-cve] WARN — trivy failed on $ref; recording placeholder" >&2
        ALL_FINDINGS="$(jq --arg src "image:$short" --arg pkg "$ref" '. += [{
            id: "AEGIS-SCAN-FAILED",
            severity: "HIGH",
            package: $pkg,
            installed_version: "unknown",
            fixed_version: null,
            primary_url: "",
            source: $src
        }]' <<<"$ALL_FINDINGS")"
        continue
    fi
    # Reshape to our stable schema + tag the source.
    SLICE="$(jq --arg src "image:$short" '
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
            source:            $src,
          }
      ]
    ' "$TMP_RAW")"
    ALL_FINDINGS="$(jq -s '.[0] + .[1]' <(echo "$ALL_FINDINGS") <(echo "$SLICE"))"
done < "$IN"

# Sort the merged list deterministically + write.
jq 'sort_by(.id, .source, .package, .installed_version)' <<<"$ALL_FINDINGS" > "$OUT"

n="$(jq 'length' "$OUT")"
echo "[image-cve] wrote $n finding(s) across $(wc -l < "$IN" | tr -d ' ') image(s) to $OUT"
exit 0
