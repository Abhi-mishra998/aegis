#!/usr/bin/env bash
# Sprint EI-15 (2026-06-20). Emit the deduplicated list of SHA256-pinned
# container images that infra/docker-compose.yml declares — one per line,
# suitable for piping into image_cve_scan.sh.
#
# Only emits images that are pinned with both a tag AND a @sha256:
# digest (`image: foo:1.2.3@sha256:…`). An unpinned `image: foo:latest`
# line is treated as a bug — the scan would race the tag.
#
# Locally-built images (no `image:` line; `build:` instead) are NOT
# listed — they are scanned via the Python SBOM path (EI-13) and via
# Trivy fs scans in security_scan.yml.
#
# Usage:
#   bash scripts/ops/list_pinned_images.sh > /tmp/pinned-images.txt
#   bash scripts/ops/image_cve_scan.sh /tmp/pinned-images.txt /tmp/img-cve.json

set -euo pipefail

COMPOSE="${1:-infra/docker-compose.yml}"

if [ ! -f "$COMPOSE" ]; then
    echo "FAIL — compose file not found: $COMPOSE" >&2
    exit 1
fi

# Match: `    image: prefix/name:tag@sha256:hex`
# - leading whitespace = 4 chars in the existing compose layout
# - reject lines that lack `@sha256:` (tag-only is forbidden — would
#   race the tag at scan time vs at deploy time)
grep -E '^[[:space:]]+image:[[:space:]]+[^[:space:]]+@sha256:[0-9a-f]{64}[[:space:]]*$' "$COMPOSE" \
    | awk '{print $2}' \
    | sort -u
