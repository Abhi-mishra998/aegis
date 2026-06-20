#!/usr/bin/env bash
# Sprint EI-10 (2026-06-20). Operator-side verifier for a downloaded
# release bundle. Same logic the ASG user_data runs at boot
# (infra/terraform/modules/asg/main.tf:73-79), exposed as a standalone
# script so:
#
#  - the operator can sanity-check a bundle before promoting it manually
#  - a CISO can re-prove "the bundle was built by Aegis's GitHub Actions
#    on the main branch" without any AWS or Aegis credentials
#  - the nightly_verify workflow can be extended to verify the *latest*
#    release bundle as part of the daily integrity check
#
# Usage:
#   bash scripts/ops/verify_signed_bundle.sh /path/to/bundle.tar.gz
#
# Expects three siblings next to the bundle:
#   <bundle>.sig     cosign signature
#   <bundle>.pem     Fulcio cert with the OIDC identity baked in
#   <bundle>.bundle  Rekor transparency-log inclusion proof
#
# Exits 0 only if the cert was issued for a `release_bundle.yml` run on
# the main branch of this repo. Any other identity (different repo,
# different branch, different workflow, a personal cosign key) fails.

set -euo pipefail

BUNDLE="${1:?path to bundle tarball required}"
REPO_SLUG="${REPO_SLUG:-Abhi-mishra998/aegis}"
WORKFLOW_PATH="${WORKFLOW_PATH:-.github/workflows/release_bundle.yml}"
REF="${REF:-refs/heads/main}"

if [ ! -f "$BUNDLE" ]; then
    echo "FAIL — $BUNDLE does not exist" >&2
    exit 1
fi
for ext in sig pem bundle; do
    if [ ! -f "${BUNDLE}.${ext}" ]; then
        echo "FAIL — missing signature artefact: ${BUNDLE}.${ext}" >&2
        echo "       (downloaded the bundle but not all 3 siblings?)" >&2
        exit 1
    fi
done

if ! command -v cosign >/dev/null 2>&1; then
    echo "FAIL — cosign not installed." >&2
    echo "       macOS:   brew install cosign" >&2
    echo "       Linux:   curl -fsSL -o /usr/local/bin/cosign \\" >&2
    echo "                  https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-amd64" >&2
    echo "                && chmod +x /usr/local/bin/cosign" >&2
    exit 2
fi

# Build the cert-identity regex from REPO_SLUG so the operator can
# override REPO_SLUG=acme-fork/aegis when verifying a fork's bundle.
CERT_IDENTITY="^https://github\.com/${REPO_SLUG}/\.github/workflows/release_bundle\.yml@${REF}\$"

echo "════════════════════════════════════════"
echo " cosign verify-blob"
echo "  bundle           : $BUNDLE"
echo "  cert identity ≈  : $CERT_IDENTITY"
echo "  oidc issuer      : https://token.actions.githubusercontent.com"
echo "════════════════════════════════════════"

if cosign verify-blob \
    --certificate     "${BUNDLE}.pem" \
    --signature       "${BUNDLE}.sig" \
    --bundle          "${BUNDLE}.bundle" \
    --certificate-identity-regexp "$CERT_IDENTITY" \
    --certificate-oidc-issuer     "https://token.actions.githubusercontent.com" \
    "$BUNDLE"; then
    echo ""
    echo "✓ Bundle verified — signed by ${REPO_SLUG} on ${REF} via release_bundle.yml."
    if [ -f "${BUNDLE}.sha256" ]; then
        expected="$(cat "${BUNDLE}.sha256" | awk '{print $1}')"
        actual="$(shasum -a 256 "$BUNDLE" | awk '{print $1}')"
        if [ "$expected" = "$actual" ]; then
            echo "✓ sha256 matches manifest."
        else
            echo "✗ sha256 MISMATCH — bundle bytes drifted from the manifest!" >&2
            echo "  expected: $expected" >&2
            echo "  actual  : $actual" >&2
            exit 4
        fi
    fi
    echo ""
    echo "Safe to deploy."
    exit 0
else
    echo "" >&2
    echo "✗ Bundle FAILED verification." >&2
    echo "  Possible causes:" >&2
    echo "    - The bundle was signed by a different workflow (not release_bundle.yml)" >&2
    echo "    - The bundle was signed from a fork or a non-main branch" >&2
    echo "    - The signature bytes were tampered with after Fulcio issued the cert" >&2
    echo "  Do NOT deploy this bundle." >&2
    exit 3
fi
