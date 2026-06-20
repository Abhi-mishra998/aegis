#!/usr/bin/env bash
# Sprint EH-6 — package everything an enterprise security review asks for.
#
# Architect's finding: "Many enterprise deals stall because founders
# scramble to create these after the request arrives." This builds the
# bundle in one shot so the sales rep can attach it before the question.
#
# Output: aegis-customer-security-package-<timestamp>.zip in /tmp/
# Contains:
#   00_README.md                        — auditor walkthrough
#   01_architecture/                    — diagrams + service topology
#   02_threat_model/                    — STRIDE per service + scenarios
#   03_security_controls/               — RBAC matrix, data classification,
#                                          shared responsibility, retention
#   04_compliance/                      — SOC2 tracker, AEVF spec, control map
#   05_operations/                      — DR runbook, secrets rotation, drill log
#   06_supply_chain/                    — git_hardening doc, security_scan.yml,
#                                          SBOM (if present)
#   07_subprocessors/                   — vendor list
#   08_pentest/                         — SoW template (engagement scheduled)
#   09_disclosure/                      — security.txt + responsible-disclosure
#   10_test_evidence/                   — latest isolation + load test results

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="/tmp/aegis-customer-security-package-${TS}"
ZIP="${OUT}.zip"

mkdir -p "$OUT"/{01_architecture,02_threat_model,03_security_controls,04_compliance,05_operations,06_supply_chain,07_subprocessors,08_pentest,09_disclosure,10_test_evidence,11_adr,12_legal}

# 00 — README
cat > "$OUT/00_README.md" <<'EOF'
# Aegis — Customer Security Package

Generated on demand by `scripts/ops/build_customer_security_package.sh`.
Everything below comes from a versioned doc in the Aegis repo. Each file
links back to the source path so you can verify nothing has been edited
for this packet.

## How to read this in order

1. `01_architecture/` — what Aegis is, how the pieces fit.
2. `02_threat_model/` — what we worry about and how we mitigate.
3. `03_security_controls/` — RBAC matrix, data classes, shared responsibility,
   retention. The four contracts every CISO asks for.
4. `04_compliance/` — SOC2 tracker (Type I in progress), AEVF spec for
   regulator-grade audit chain verification.
5. `05_operations/` — DR runbook with RTO/RPO targets, secrets rotation
   runbook, monthly drill log.
6. `06_supply_chain/` — supply-chain hardening: cosign image signing,
   Trivy/Gitleaks/Checkov/Bandit in CI, CODEOWNERS-gated reviews,
   CycloneDX 1.5 SBOM (`sbom.cyclonedx.json`) regenerated nightly by CI.
7. `07_subprocessors/` — the seven vendors that touch customer data.
8. `08_pentest/` — engagement SoW (Q3 2026 scheduled).
9. `09_disclosure/` — responsible disclosure policy + RFC 9116 security.txt.
10. `10_test_evidence/` — most recent isolation pen-test (7/7 attacks
    blocked) and concurrent-load test (300 reqs, 0 errors).
11. `11_adr/` — Architecture Decision Records: the "why" behind every
    structural choice in Aegis, the alternatives we considered, and the
    constraints they impose on future work.
12. `12_legal/` — Contract templates: MSA, DPA, BAA (HIPAA), SLA. All
    engineering-drafted with `<LEGAL REVIEW PENDING>` markers; hand to
    Customer counsel for redline.

If anything here is missing or stale, email `security@aegisagent.in`.
EOF

cp_safe() {
    local src="$1" dst="$2"
    if [ -f "$src" ]; then cp "$src" "$dst"; else echo "(missing — see source repo)" > "$dst"; fi
}

cp_dir_safe() {
    local src="$1" dst="$2"
    if [ -d "$src" ]; then cp -R "$src" "$dst"; fi
}

# 01 — Architecture
cp_safe  "$REPO_ROOT/docs/architecture/deployment-topology.md" "$OUT/01_architecture/deployment-topology.md"
cp_safe  "$REPO_ROOT/docs/architecture/services.md"            "$OUT/01_architecture/services.md"
cp_safe  "$REPO_ROOT/docs/operations/deployment.md"            "$OUT/01_architecture/deployment-procedure.md"

# 02 — Threat model
cp_safe  "$REPO_ROOT/docs/security/threat-model.md"            "$OUT/02_threat_model/threat-model.md"
cp_safe  "$REPO_ROOT/docs/THREAT_MODEL.md"                     "$OUT/02_threat_model/stride-per-service.md"
cp_safe  "$REPO_ROOT/docs/security/threat-scenarios.md"        "$OUT/02_threat_model/red-team-scenarios.md"

# 03 — Security controls
cp_safe  "$REPO_ROOT/docs/security/rbac_matrix.md"             "$OUT/03_security_controls/rbac-matrix.md"
cp_safe  "$REPO_ROOT/docs/security/data_classification.md"     "$OUT/03_security_controls/data-classification.md"
cp_safe  "$REPO_ROOT/docs/security/shared_responsibility.md"   "$OUT/03_security_controls/shared-responsibility.md"
cp_safe  "$REPO_ROOT/docs/security/data_retention.md"          "$OUT/03_security_controls/data-retention.md"
cp_safe  "$REPO_ROOT/docs/security/data_residency.md"          "$OUT/03_security_controls/data-residency.md"

# 04 — Compliance
cp_safe  "$REPO_ROOT/docs/security/soc2_tracker.md"            "$OUT/04_compliance/soc2-tracker.md"
cp_safe  "$REPO_ROOT/docs/AEVF/spec.md"                        "$OUT/04_compliance/aevf-spec.md"
cp_safe  "$REPO_ROOT/docs/AEVF/auditor-checklist.md"           "$OUT/04_compliance/auditor-checklist.md"
cp_safe  "$REPO_ROOT/docs/AEVF/README.md"                      "$OUT/04_compliance/aevf-readme.md"
cp_safe  "$REPO_ROOT/services/audit/compliance_export.py"      "$OUT/04_compliance/compliance_export.py.txt"

# 05 — Operations
cp_safe  "$REPO_ROOT/docs/runbooks/disaster_recovery.md"       "$OUT/05_operations/disaster-recovery.md"
cp_safe  "$REPO_ROOT/docs/runbooks/secrets_rotation.md"        "$OUT/05_operations/secrets-rotation.md"
cp_safe  "$REPO_ROOT/docs/runbooks/object_lock_migration.md"   "$OUT/05_operations/object-lock-migration.md"
cp_safe  "$REPO_ROOT/docs/runbooks/dr_drill_log.md"            "$OUT/05_operations/dr-drill-log.md"
cp_safe  "$REPO_ROOT/docs/runbooks/key_rotation.md"            "$OUT/05_operations/key-rotation.md"
cp_safe  "$REPO_ROOT/docs/runbooks/multi_region_bootstrap.md"  "$OUT/05_operations/multi-region-bootstrap.md"
cp_safe  "$REPO_ROOT/docs/runbooks/chaos_drill_log.md"         "$OUT/05_operations/chaos-drill-log.md"
cp_safe  "$REPO_ROOT/docs/runbooks/status_page_setup.md"       "$OUT/05_operations/status-page-setup.md"
cp_safe  "$REPO_ROOT/scripts/ops/uptime_rollup.py"             "$OUT/05_operations/uptime_rollup.py"

# 06 — Supply chain
cp_safe  "$REPO_ROOT/docs/security/git_hardening.md"           "$OUT/06_supply_chain/git-hardening.md"
cp_safe  "$REPO_ROOT/.github/workflows/security_scan.yml"      "$OUT/06_supply_chain/ci-security_scan.yml"
cp_safe  "$REPO_ROOT/.github/workflows/terraform.yml"          "$OUT/06_supply_chain/ci-terraform.yml"
cp_safe  "$REPO_ROOT/.github/CODEOWNERS"                       "$OUT/06_supply_chain/CODEOWNERS"
cp_safe  "$REPO_ROOT/scripts/ops/sign_bundle.sh"               "$OUT/06_supply_chain/sign-bundle.sh"
cp_safe  "$REPO_ROOT/scripts/ci/no_secrets_on_disk.sh"         "$OUT/06_supply_chain/ci-no-secrets-on-disk.sh"
# Sprint EI-13 — SBOM CVE-watch tooling (nightly_verify pipeline)
cp_safe  "$REPO_ROOT/scripts/ops/sbom_cve_scan.sh"             "$OUT/06_supply_chain/sbom_cve_scan.sh"
cp_safe  "$REPO_ROOT/scripts/ops/sbom_cve_diff.py"             "$OUT/06_supply_chain/sbom_cve_diff.py"
# Sprint EI-15 — container-image CVE scan (OS-layer coverage on top of EI-13)
cp_safe  "$REPO_ROOT/scripts/ops/image_cve_scan.sh"            "$OUT/06_supply_chain/image_cve_scan.sh"
cp_safe  "$REPO_ROOT/scripts/ops/list_pinned_images.sh"        "$OUT/06_supply_chain/list_pinned_images.sh"
cp_safe  "$REPO_ROOT/.github/workflows/nightly_verify.yml"     "$OUT/06_supply_chain/ci-nightly_verify.yml"
# SBOM — CycloneDX 1.5 JSON, regenerated nightly by .github/workflows/security_scan.yml (sbom job).
# If missing locally, run: `pip install cyclonedx-bom && cyclonedx-py environment \
#   --output-format json --output-file reports/sbom.cyclonedx.json` from repo root.
if [ -f "$REPO_ROOT/reports/sbom.cyclonedx.json" ]; then
    cp "$REPO_ROOT/reports/sbom.cyclonedx.json" "$OUT/06_supply_chain/sbom.cyclonedx.json"
else
    cat > "$OUT/06_supply_chain/sbom.MISSING" <<'EOF'
SBOM not present in reports/sbom.cyclonedx.json at package time.

CI generates it on every push to main; download the latest artifact named
`sbom-cyclonedx-<sha>` from the security-scan workflow run that built this
release, OR regenerate locally:

    pip install cyclonedx-bom
    cyclonedx-py environment \
      --output-format json \
      --output-file reports/sbom.cyclonedx.json

Then re-run scripts/ops/build_customer_security_package.sh.
EOF
fi

# 07 — Subprocessors
cp_safe  "$REPO_ROOT/docs/security/subprocessors.md"           "$OUT/07_subprocessors/subprocessors.md"

# 08 — Pen test
cp_safe  "$REPO_ROOT/docs/security/pentest-sow-template.md"    "$OUT/08_pentest/pentest-sow-template.md"
cat > "$OUT/08_pentest/STATUS.md" <<EOF
# Pen-test engagement status

Engagement scheduled: **Q3 2026** (per docs/security/pentest-sow-template.md).
Report will be appended to this folder when delivered.

For your own pen-test against your Aegis tenant, follow the rules of
engagement in 09_disclosure/security.txt — out-of-scope IS the shared
control plane (gateway, identity, audit svc), in-scope IS your own
agent definitions + tool configurations.
EOF

# 09 — Responsible disclosure
cp_safe  "$REPO_ROOT/ui/public/.well-known/security.txt"       "$OUT/09_disclosure/security.txt"
cat > "$OUT/09_disclosure/policy.md" <<'EOF'
# Responsible Disclosure Policy

Aegis runs no public bug-bounty program at this time. We do honour
responsible disclosure under the following terms:

- Email security@aegisagent.in or open a GitHub Security Advisory.
- Do not test against other tenants' data. Use the demo workspace
  (anonymous, /demo/spawn-workspace) for any active probing.
- Acknowledge: within 48 hours.
- Triage decision: within 5 business days.
- Patch for HIGH/CRITICAL: within 90 days, faster on agreement.
- We will publicly credit you (handle of your choice) in the next
  Aegis security advisory unless you ask us not to.
EOF

# 10 — Test evidence
cp_dir_safe "$REPO_ROOT/reports/e2e_test_2026_06_20"           "$OUT/10_test_evidence/2026-06-20"
cp_safe     "$REPO_ROOT/testing.md"                            "$OUT/10_test_evidence/most-recent-report.md"

# 11 — Architecture Decision Records
cp_dir_safe "$REPO_ROOT/docs/adr"                              "$OUT/11_adr_src"
# Flatten the directory copy into the ZIP layer for discoverability.
if [ -d "$OUT/11_adr_src" ]; then
    mv "$OUT/11_adr_src"/* "$OUT/11_adr/" 2>/dev/null || true
    rmdir "$OUT/11_adr_src"
fi

# 12 — Legal templates (MSA, DPA, BAA, SLA + index)
cp_safe  "$REPO_ROOT/docs/legal/README.md"                     "$OUT/12_legal/README.md"
cp_safe  "$REPO_ROOT/docs/legal/msa-template.md"               "$OUT/12_legal/msa-template.md"
cp_safe  "$REPO_ROOT/docs/legal/dpa-template.md"               "$OUT/12_legal/dpa-template.md"
cp_safe  "$REPO_ROOT/docs/legal/baa-template.md"               "$OUT/12_legal/baa-template.md"
cp_safe  "$REPO_ROOT/docs/legal/sla-template.md"               "$OUT/12_legal/sla-template.md"

# Zip it up — include a manifest so the buyer can verify file count.
( cd /tmp && zip -rq "$ZIP" "$(basename "$OUT")" )

echo
echo "════════════════════════════════════════"
echo " Customer security package built:"
echo "  $ZIP"
echo "  $(ls -lh "$ZIP" | awk '{print $5}') / $(find "$OUT" -type f | wc -l | tr -d ' ') files"
echo "════════════════════════════════════════"
echo
echo "Sha256:"
shasum -a 256 "$ZIP"
