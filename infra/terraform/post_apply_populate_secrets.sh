#!/usr/bin/env bash
# Post-apply: pump preserved secrets from /tmp/aegis-keep-this/ into the
# fresh SSM Parameter Store + Secrets Manager entries.
#
# Idempotent: re-running is safe (put-parameter --overwrite, put-secret-value).
# Plain-bash compatible (no associative arrays - macOS bash 3.2 friendly).

set -euo pipefail

SRC=/tmp/aegis-keep-this
REGION=ap-south-1

if [[ ! -d "$SRC" ]]; then
    echo "FATAL: $SRC missing - no preserved secrets to restore." >&2
    exit 1
fi

# ── SSM Parameter Store ────────────────────────────────────────────────
# Each line is: PATH<TAB>SOURCE_FILE
SSM_LINES="\
/aegis-prodha/aegis/auth-provider	__aegis-prodha__aegis__auth-provider.value
/aegis-prodha/anthropic/upstream-key	__aegis-prodha__anthropic__upstream-key.value
/aegis-prodha/clerk/secret-key	__aegis-prodha__clerk__secret-key.value
/aegis-prodha/clerk/frontend-api	__aegis-prodha__clerk__frontend-api.value
/aegis-prodha/clerk/issuer	__aegis-prodha__clerk__issuer.value
/aegis-prodha/clerk/jwks-url	__aegis-prodha__clerk__jwks-url.value
/aegis-prodha/clerk/jwt-template	__aegis-prodha__clerk__jwt-template.value
/aegis-prodha/clerk/publishable-key	__aegis-prodha__clerk__publishable-key.value
/aegis-prodha/clerk/webhook-secret	__aegis-prodha__clerk__webhook-secret.value
/aegis-prodha/docker/hub-pat	__aegis-prodha__docker__hub-pat.value
/aegis-prodha/docker/hub-user	__aegis-prodha__docker__hub-user.value
/aegis-prodha/pypi/token	__aegis-prodha__pypi__token.value
/aegis-prodha/stripe/pro-price-id	__aegis-prodha__stripe__pro-price-id.value
/aegis-prodha/stripe/enterprise-price-id	__aegis-prodha__stripe__enterprise-price-id.value
/aegis-prodha/stripe/secret-key	__aegis-prodha__stripe__secret-key.value
/aegis-prodha/stripe/webhook-secret	__aegis-prodha__stripe__webhook-secret.value
/aegis-prodha/receipt-signing-key	__acp-prodha__receipt-signing-key.value"

echo "$SSM_LINES" | while IFS=$'\t' read -r path filename; do
    [[ -z "$path" ]] && continue
    file="$SRC/$filename"
    if [[ ! -f "$file" ]]; then
        echo "WARN: $file missing - skipping $path" >&2
        continue
    fi
    val=$(cat "$file")
    if [[ -z "$val" ]]; then
        echo "WARN: $file empty - skipping $path" >&2
        continue
    fi
    aws ssm put-parameter \
        --region "$REGION" \
        --name "$path" \
        --value "$val" \
        --type SecureString \
        --overwrite >/dev/null
    echo "OK SSM $path"
done

# ── Secrets Manager (only operator-supplied placeholders) ─────────────
SEC_LINES="\
aegis-prod-stripe-webhook-secret	secret__acp-prodha__stripe_webhook_secret.value
aegis-prod-groq-api-key	secret__acp-prodha__groq_api_key.value"

echo "$SEC_LINES" | while IFS=$'\t' read -r name filename; do
    [[ -z "$name" ]] && continue
    file="$SRC/$filename"
    if [[ ! -f "$file" ]]; then
        echo "WARN: $file missing - skipping $name" >&2
        continue
    fi
    val=$(cat "$file")
    if [[ -z "$val" ]]; then
        echo "WARN: $file empty - skipping $name" >&2
        continue
    fi
    aws secretsmanager put-secret-value \
        --region "$REGION" \
        --secret-id "$name" \
        --secret-string "$val" >/dev/null
    echo "OK SEC $name"
done

echo ""
echo "Done. Verify with:"
echo "  aws ssm get-parameter --name /aegis-prodha/anthropic/upstream-key --with-decryption --query Parameter.Value --output text"
echo "  aws secretsmanager get-secret-value --secret-id aegis-prod-groq-api-key --query SecretString --output text"
