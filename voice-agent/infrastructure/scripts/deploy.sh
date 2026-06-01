#!/bin/bash
# Ship code from the dev machine to the EC2 instance, install venv, run ingest,
# (re)start the agent. Idempotent — re-run after code or doc changes.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_lib.sh"

load_aws_env
PEM="$(tf_pem_path)"
PUBLIC_IP="$(tf_public_ip)"
SSH="ssh -i $PEM -o StrictHostKeyChecking=accept-new ubuntu@$PUBLIC_IP"

# 1. rsync code + docs to the instance, excluding venv/build artefacts.
rsync -az --delete \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude 'chroma_db/' \
  --exclude 'bm25_index/' \
  --exclude '.env.local' \
  -e "ssh -i $PEM -o StrictHostKeyChecking=accept-new" \
  "$REPO_ROOT/agent/" "ubuntu@$PUBLIC_IP:/opt/aegis/agent/"

rsync -az --delete \
  -e "ssh -i $PEM -o StrictHostKeyChecking=accept-new" \
  "$REPO_ROOT/docs/" "ubuntu@$PUBLIC_IP:/opt/aegis/docs/"

# 2. On the instance: create venv (idempotent), install deps, fetch secrets,
#    rebuild indexes, restart the agent service.
$SSH 'bash -s' <<'REMOTE'
set -euxo pipefail
cd /opt/aegis/agent

if [ ! -d .venv ]; then
  python3.12 -m venv .venv
fi
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet \
  "livekit-agents[deepgram,cartesia,openai,silero,turn-detector]~=1.5" \
  "chromadb>=0.5" \
  "sentence-transformers>=3.0" \
  "rank-bm25>=0.2.2" \
  "torch>=2.2,<3" \
  "python-dotenv>=1.0"

# Pull provider keys from Secrets Manager into .env.local
sudo systemctl start aegis-fetch-secrets.service
sudo systemctl status aegis-fetch-secrets.service --no-pager | head -5

# Pre-download model weights so first conversation isn't cold.
.venv/bin/python -m livekit.agents download-files || true
.venv/bin/python -c "from sentence_transformers import SentenceTransformer, CrossEncoder; SentenceTransformer('all-MiniLM-L6-v2'); CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"

# Build the hybrid RAG indexes from /opt/aegis/docs.
.venv/bin/python src/ingest.py /opt/aegis/docs

# Restart the agent service (was failing before code was here).
sudo systemctl restart aegis-agent.service
sleep 2
sudo systemctl status aegis-agent.service --no-pager | head -15
REMOTE

echo
echo "Deployed. Tail logs: ./scripts/status.sh"
