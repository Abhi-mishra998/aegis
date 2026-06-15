#!/usr/bin/env bash
# Sprint 9 — CI guard: README's "integrations" section must not list a
# SIEM target that the services/audit/siem.py dispatcher doesn't ship.
#
# Closes the audit C15 finding ("README claims Elastic / Sentinel /
# Chronicle; only Splunk + Datadog ship") for good — even after Sprint 2b
# shipped those forwarders, future drift between README + code surfaces
# here before a buyer reads it.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

# 1. Pull every forwarder class declared in siem.py and normalise
# the name to its canonical vendor stem (SplunkHECForwarder → splunk,
# AzureSentinelForwarder → sentinel, etc.).
SHIPPED=$(grep -oE 'class +[A-Z][A-Za-z]+Forwarder' services/audit/siem.py \
    | awk '{print tolower($2)}' \
    | sed -E 's/(hec|cloud|api|azure)?forwarder$//' \
    | sed -E 's/^(microsoft|google|azure|aws)//' \
    | sort -u)

echo "[evidence-parity] SIEM targets in code:"
for t in ${SHIPPED}; do echo "  - ${t}"; done

# 2. Pull the SIEM targets the docs / README claim. The doc page is the
# single source of truth (docs/integrations/evidence-export.md).
DOC_SOURCE="docs/integrations/evidence-export.md"
if [[ ! -f "${DOC_SOURCE}" ]]; then
    echo "[evidence-parity] FAIL — ${DOC_SOURCE} missing" >&2
    exit 1
fi

CLAIMED=$(grep -oE 'Splunk|Datadog|Elastic|Sentinel|Chronicle' "${DOC_SOURCE}" \
    | awk '{print tolower($0)}' \
    | sort -u)

echo "[evidence-parity] SIEM targets claimed in docs:"
for t in ${CLAIMED}; do echo "  - ${t}"; done

# 3. The set of claimed targets must be a subset of shipped targets.
MISSING=$(comm -23 <(echo "${CLAIMED}") <(echo "${SHIPPED}"))
if [[ -n "${MISSING}" ]]; then
    echo "[evidence-parity] FAIL — docs claim these but code doesn't ship them:" >&2
    echo "${MISSING}" | sed 's/^/  - /' >&2
    echo "" >&2
    echo "Either implement the missing forwarder in services/audit/siem.py" >&2
    echo "OR remove the claim from ${DOC_SOURCE}." >&2
    exit 1
fi

echo "[evidence-parity] OK — every documented SIEM target ships in code."
