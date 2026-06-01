#!/bin/bash
# Shared helpers for the operations scripts. Sourced by plan/apply/start/stop/ssh/deploy.
set -euo pipefail

# Resolve repo root + paths relative to this script.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$INFRA_DIR/.." && pwd)"
AGENT_ENV="$REPO_ROOT/agent/.env.local"
AWS_ENV="$INFRA_DIR/.env.aws.local"

require_file() {
  if [ ! -f "$1" ]; then
    echo "FATAL: $1 not found." >&2
    exit 1
  fi
}

# Read a KEY=value line out of a .env file, ignoring comments and quotes.
env_get() {
  local file="$1" key="$2"
  local line
  line="$(grep -E "^${key}=" "$file" || true)"
  if [ -z "$line" ]; then
    echo "FATAL: $key not set in $file." >&2
    exit 1
  fi
  echo "${line#${key}=}" | sed -e 's/^["'\'']//' -e 's/["'\'']$//'
}

# Export AWS credentials from infrastructure/.env.aws.local so the AWS provider
# + AWS CLI both pick them up.
load_aws_env() {
  require_file "$AWS_ENV"
  set -a
  # shellcheck disable=SC1090
  source "$AWS_ENV"
  set +a
  : "${AWS_ACCESS_KEY_ID:?missing in $AWS_ENV}"
  : "${AWS_SECRET_ACCESS_KEY:?missing in $AWS_ENV}"
  : "${AWS_DEFAULT_REGION:?missing in $AWS_ENV}"
  export AWS_REGION="$AWS_DEFAULT_REGION"
}

# Read agent runtime secrets from agent/.env.local and expose them as TF_VAR_*.
# Never logs the values.
load_tf_vars_from_agent_env() {
  require_file "$AGENT_ENV"
  export TF_VAR_livekit_url="$(env_get "$AGENT_ENV" LIVEKIT_URL)"
  export TF_VAR_livekit_api_key="$(env_get "$AGENT_ENV" LIVEKIT_API_KEY)"
  export TF_VAR_livekit_api_secret="$(env_get "$AGENT_ENV" LIVEKIT_API_SECRET)"
  export TF_VAR_deepgram_api_key="$(env_get "$AGENT_ENV" DEEPGRAM_API_KEY)"
  export TF_VAR_cartesia_api_key="$(env_get "$AGENT_ENV" CARTESIA_API_KEY)"
  export TF_VAR_groq_api_key="$(env_get "$AGENT_ENV" GROQ_API_KEY)"
  # Optional fallback — empty string is fine (matches variables_secrets.tf default).
  TF_VAR_google_api_key="$(grep -E '^GOOGLE_API_KEY=' "$AGENT_ENV" 2>/dev/null | sed -E 's/^GOOGLE_API_KEY=//' | sed -e 's/^["'\'']//' -e 's/["'\'']$//')"
  export TF_VAR_google_api_key
}

# Fetch the instance_id from Terraform outputs (after apply).
tf_instance_id() {
  ( cd "$INFRA_DIR" && terraform output -raw instance_id )
}
tf_public_ip() {
  ( cd "$INFRA_DIR" && terraform output -raw public_ip )
}
tf_pem_path() {
  ( cd "$INFRA_DIR" && terraform output -raw pem_path )
}
