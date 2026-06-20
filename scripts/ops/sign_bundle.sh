#!/usr/bin/env bash
# Sprint EH-4 — Sign a release bundle with cosign keyless (Sigstore OIDC).
# Closes architect finding: "no cryptographic chain from commit to image".
#
# Inputs:
#   $1  path to bundle tarball (e.g. /tmp/bundle-abc123.tar.gz)
#   $2  release id (defaults to git short SHA)
#
# Produces alongside the tarball:
#   <bundle>.sig     — cosign signature
#   <bundle>.pem     — signing certificate (incl. Fulcio chain)
#   <bundle>.bundle  — transparency log inclusion proof
#
# Verification on the EC2 host (user_data) uses:
#   cosign verify-blob --certificate <pem> --signature <sig> --bundle <bundle> \
#                      --certificate-identity-regexp "^https://github\.com/Abhi-mishra998/aegis/" \
#                      --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
#                      <bundle.tar.gz>
#
# Pre-req:
#   COSIGN_EXPERIMENTAL=1 (keyless mode)
#   GITHUB_TOKEN env var (provided automatically inside GitHub Actions)
#   For local signing: cosign login to a Sigstore-supported OIDC issuer
#     (Google, GitHub, Microsoft).

set -euo pipefail

BUNDLE="${1:?path to bundle tarball required}"
RELEASE_ID="${2:-$(git rev-parse --short=12 HEAD)}"

if [ ! -f "$BUNDLE" ]; then
    echo "ERROR: $BUNDLE does not exist" >&2
    exit 1
fi

if ! command -v cosign >/dev/null 2>&1; then
    echo "ERROR: cosign not installed. Install: https://docs.sigstore.dev/cosign/installation/" >&2
    exit 2
fi

export COSIGN_EXPERIMENTAL=1

echo "[sign-bundle] signing ${BUNDLE} (release=${RELEASE_ID})"

# Keyless signing — uses OIDC token, no long-lived signing keys to leak.
# Inside GitHub Actions, cosign auto-discovers the OIDC token from
# GITHUB_ACTIONS=true + ACTIONS_ID_TOKEN_REQUEST_URL.
cosign sign-blob \
    --yes \
    --bundle "${BUNDLE}.bundle" \
    --output-certificate "${BUNDLE}.pem" \
    --output-signature   "${BUNDLE}.sig" \
    "${BUNDLE}"

# SHA256 of the bundle for the integrity manifest
shasum -a 256 "${BUNDLE}" | awk '{print $1}' > "${BUNDLE}.sha256"

echo "[sign-bundle] DONE"
echo "  signature   : ${BUNDLE}.sig"
echo "  certificate : ${BUNDLE}.pem"
echo "  bundle      : ${BUNDLE}.bundle"
echo "  sha256      : ${BUNDLE}.sha256"

echo
echo "[sign-bundle] To verify on a downstream host:"
cat <<EOF
  cosign verify-blob \\
      --certificate "${BUNDLE}.pem" \\
      --signature   "${BUNDLE}.sig" \\
      --bundle      "${BUNDLE}.bundle" \\
      --certificate-identity-regexp "^https://github\\.com/Abhi-mishra998/aegis/" \\
      --certificate-oidc-issuer     "https://token.actions.githubusercontent.com" \\
      "${BUNDLE}"
EOF
