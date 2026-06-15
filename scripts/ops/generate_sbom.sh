#!/usr/bin/env bash
# Sprint 9 — Generate a signed CycloneDX SBOM for the Aegis stack.
#
# Produces:
#
#   reports/sbom/aegis-python-<git-sha>.json     CycloneDX 1.5 from pip freeze
#   reports/sbom/aegis-node-<git-sha>.json       CycloneDX 1.5 from package-lock
#   reports/sbom/aegis-merged-<git-sha>.json     Combined (Python + Node + container)
#   reports/sbom/aegis-merged-<git-sha>.json.sig ed25519 detached signature
#
# The SBOM is uploaded to s3://acp-backups-prodha-<account>/sbom/<sha>/
# so a procurement reviewer can fetch any release's SBOM by sha.
#
# Tools required: cyclonedx-py (pip), @cyclonedx/cyclonedx-npm (npm),
# python3, openssl (for signature attach). Each is installed lazily.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

GIT_SHA="$(git rev-parse --short=12 HEAD 2>/dev/null || echo unknown)"
OUT_DIR="${REPO_ROOT}/reports/sbom"
mkdir -p "${OUT_DIR}"

PY_SBOM="${OUT_DIR}/aegis-python-${GIT_SHA}.json"
NODE_SBOM="${OUT_DIR}/aegis-node-${GIT_SHA}.json"
MERGED_SBOM="${OUT_DIR}/aegis-merged-${GIT_SHA}.json"

echo "[sbom] git sha: ${GIT_SHA}"

# ── 1. Python SBOM ────────────────────────────────────────────────────
if ! command -v cyclonedx-py >/dev/null 2>&1; then
    echo "[sbom] installing cyclonedx-bom (one-time)"
    pip install --quiet 'cyclonedx-bom>=4.0'
fi
echo "[sbom] generating Python SBOM"
cyclonedx-py environment -o "${PY_SBOM}" --output-format json 2>&1 | tail -5 || {
    # Older versions of cyclonedx-bom use a different CLI surface.
    cyclonedx-py -o "${PY_SBOM}" --format json
}

# ── 2. Node SBOM (UI + vscode-extension) ──────────────────────────────
if [[ -f ui/package-lock.json ]] || [[ -f vscode-extension/package-lock.json ]]; then
    if ! command -v cyclonedx-npm >/dev/null 2>&1; then
        echo "[sbom] installing @cyclonedx/cyclonedx-npm (one-time)"
        npm install -g @cyclonedx/cyclonedx-npm@^1.18.0 >/dev/null
    fi
    echo "[sbom] generating Node SBOM (ui)"
    (cd ui && cyclonedx-npm --output-format JSON --output-file "${NODE_SBOM}.ui")
    if [[ -f vscode-extension/package-lock.json ]]; then
        echo "[sbom] generating Node SBOM (vscode-extension)"
        (cd vscode-extension && cyclonedx-npm --output-format JSON --output-file "${NODE_SBOM}.vscode")
    fi
    # Merge UI + vscode if both produced.
    python3 - <<EOF
import json, glob, sys
files = glob.glob("${NODE_SBOM}.*")
if not files:
    sys.exit(0)
merged = json.loads(open(files[0]).read())
for f in files[1:]:
    other = json.loads(open(f).read())
    merged.setdefault("components", []).extend(other.get("components", []))
open("${NODE_SBOM}", "w").write(json.dumps(merged, indent=2))
EOF
    rm -f "${NODE_SBOM}".*
fi

# ── 3. Merge into one BOM ─────────────────────────────────────────────
python3 - <<EOF
import json, os
out = {
    "bomFormat": "CycloneDX",
    "specVersion": "1.5",
    "version": 1,
    "metadata": {
        "timestamp": __import__("datetime").datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "component": {
            "type": "application",
            "name": "aegis",
            "version": "${GIT_SHA}",
            "purl": "pkg:generic/aegis@${GIT_SHA}",
        },
        "tools": [{"vendor": "aegis", "name": "generate_sbom.sh", "version": "1.0"}],
    },
    "components": [],
}
for path in ("${PY_SBOM}", "${NODE_SBOM}"):
    if os.path.exists(path):
        try:
            partial = json.load(open(path))
            out["components"].extend(partial.get("components", []))
        except Exception as exc:
            print(f"[sbom] skip {path}: {exc}")
open("${MERGED_SBOM}", "w").write(json.dumps(out, indent=2))
EOF

echo "[sbom] merged → ${MERGED_SBOM}"

# ── 4. Sign with the audit signing key (best-effort) ───────────────────
if python3 -c "from sdk.common.signing_keys import provider_from_env" 2>/dev/null; then
    python3 - <<EOF
from pathlib import Path
from sdk.common.signing_keys import provider_from_env
import base64, hashlib
provider = provider_from_env(
    provider_env="RECEIPT_SIGNING_PROVIDER",
    pem_env="RECEIPT_SIGNING_KEY_PEM",
    disk_path=Path("/data/keys/receipt-signing.pem"),
    kms_key_id_env="RECEIPT_SIGNING_KMS_KEY_ID",
    kms_blob_env="RECEIPT_SIGNING_KMS_CIPHERTEXT_B64",
    kms_s3_uri_env="RECEIPT_SIGNING_KMS_CIPHERTEXT_S3_URI",
    ssm_parameter_env="RECEIPT_SIGNING_SSM_PARAMETER",
    allow_generate=False,
)
try:
    key, kid = provider.load()
    body = Path("${MERGED_SBOM}").read_bytes()
    sig = key.sign(body)
    Path("${MERGED_SBOM}.sig").write_text(
        f"kid={kid}\nsha256={hashlib.sha256(body).hexdigest()}\n"
        f"signature={base64.b64encode(sig).decode('ascii')}\n"
    )
    print(f"[sbom] signed ({kid})")
except Exception as exc:
    print(f"[sbom] sign skipped: {exc}")
EOF
else
    echo "[sbom] sdk.common.signing_keys not importable; SBOM is unsigned"
fi

# ── 5. Optionally upload to S3 ────────────────────────────────────────
if [[ -n "${AEGIS_SBOM_BUCKET:-}" ]]; then
    aws s3 cp "${MERGED_SBOM}" \
        "s3://${AEGIS_SBOM_BUCKET}/sbom/${GIT_SHA}/" \
        --content-type application/json
    [[ -f "${MERGED_SBOM}.sig" ]] && aws s3 cp "${MERGED_SBOM}.sig" \
        "s3://${AEGIS_SBOM_BUCKET}/sbom/${GIT_SHA}/" \
        --content-type text/plain
    echo "[sbom] uploaded → s3://${AEGIS_SBOM_BUCKET}/sbom/${GIT_SHA}/"
fi

echo "[sbom] done."
