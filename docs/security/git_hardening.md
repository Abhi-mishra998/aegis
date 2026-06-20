# Git + repo hardening — operator runbook

Sprint EH-4 closes the architect's "no chain of cryptographic custody from commit to production" finding. This doc is the operator playbook for applying the GitHub-side controls; the in-repo controls (CODEOWNERS, `.gitleaks.toml`, security_scan.yml workflow) are already merged.

## 1 · Branch protection on `main`

Run via the GitHub CLI (or set the same in the web UI under Settings → Branches):

```bash
gh api -X PUT repos/Abhi-mishra998/aegis/branches/main/protection \
  -F required_status_checks.strict=true \
  -F required_status_checks.contexts[]='test (unit)' \
  -F required_status_checks.contexts[]='Trivy filesystem CVE scan' \
  -F required_status_checks.contexts[]='Gitleaks secret-pattern scan' \
  -F required_status_checks.contexts[]='Checkov IaC scan (Terraform + Dockerfile)' \
  -F required_status_checks.contexts[]='Bandit Python AST security scan' \
  -F enforce_admins=true \
  -F required_pull_request_reviews.dismiss_stale_reviews=true \
  -F required_pull_request_reviews.require_code_owner_reviews=true \
  -F required_pull_request_reviews.required_approving_review_count=1 \
  -F required_linear_history=true \
  -F allow_force_pushes=false \
  -F allow_deletions=false \
  -F required_signatures=true
```

`required_signatures=true` is the critical one — every commit on main must carry a verified GPG or SSH signature. Combined with `enforce_admins=true` this prevents even the CTO from bypassing the rule.

## 2 · Signed-commits for every committer

Every committer needs a verified signing key on their GitHub account.

### Generate (SSH-signing path — recommended, no PGP)

```bash
ssh-keygen -t ed25519 -C "abhishek@aegisagent.in" -f ~/.ssh/aegis-commit
git config --global user.signingkey ~/.ssh/aegis-commit.pub
git config --global commit.gpgsign true
git config --global tag.gpgsign true
git config --global gpg.format ssh
```

Then upload `~/.ssh/aegis-commit.pub` to https://github.com/settings/ssh/new with type = "Signing key".

### Verify

```bash
git log --show-signature -1
```

Should print "Good signature from …".

### CI guard

The default `actions/checkout@v4` action already verifies signatures when `persist-credentials: true`. To be explicit, add to every workflow that mutates state:

```yaml
- uses: actions/checkout@v4
  with:
    ssh-strict: true
    persist-credentials: false
```

## 3 · CI scan suite (security_scan.yml)

Runs on every PR + every push to main + nightly. Four scanners:

| Scanner | What | Fails build on |
|---------|------|----------------|
| Trivy fs | CVE check on pip/npm/Dockerfile | HIGH/CRITICAL unfixed CVEs |
| Gitleaks | Secret-pattern scan | Any match outside `.gitleaks.toml` allowlist |
| Checkov | Terraform + Dockerfile misconfig | HIGH/CRITICAL rule, unless skip-listed |
| Bandit | Python AST security smells | HIGH-severity finding |

Suppression workflow:

1. **Real but accepted risk** → add to scanner-specific ignore file with an `expires:` comment + tracking issue.
2. **False positive** → upstream PR to the scanner OR add to `.trivyignore` / `.gitleaks.toml` / `skip_check`.
3. **Genuine vuln, must fix** → patch in same PR, never temporarily suppress.

## 4 · Bundle signing (cosign keyless OIDC, Sprint EI-10)

The release bundle that EC2 pulls at boot is signed end-to-end via
GitHub Actions OIDC — no long-lived signing keys exist anywhere in the
pipeline. Pipeline:

```
git push origin main
  → GitHub Actions release_bundle.yml
    → permissions.id-token: write  (workflow gets a short-lived OIDC token)
    → npm run build  +  scripts/ops/build_release_bundle.sh
    → cosign sign-blob --yes  ← Fulcio issues a 10-min cert bound to the
                                workflow URL + commit SHA; signature
                                inclusion logged in Rekor
    → aws s3 cp  bundle.tar.gz + .sig + .pem + .bundle  to
                 s3://aegis-prod-backups-628478946931-v2/releases/
    → aws ssm put-parameter /aegis/prod/current_bundle_sha
EC2 user_data (next ASG instance refresh)
  → aws s3 cp  bundle.tar.gz + 3 siblings
  → cosign verify-blob  ← refuses to extract if cert-identity wrong
  → tar xz + docker compose up
```

The verify step on EC2 (`infra/terraform/modules/asg/main.tf:73-79`):

- Requires `--certificate-identity-regexp '^https://github\.com/Abhi-mishra998/aegis/'`
  — only THIS repo's workflows can sign things this fleet will accept.
- Requires `--certificate-oidc-issuer 'https://token.actions.githubusercontent.com'`
  — only Fulcio-issued certs from GitHub OIDC, never a personal cosign key.
- If the SSM gate `/aegis/prod/require_signed_bundle == "true"`, the
  deploy aborts on any verification failure. Default `false` for the
  migration window; flip to `true` once the first release_bundle.yml run
  has landed a signed bundle in S3 (see `testing.md` §OP-4).

Any third party with cosign installed can re-verify the chain from outside:

```bash
bash scripts/ops/verify_signed_bundle.sh /path/to/bundle.tar.gz
# Pass iff the cert was issued for a release_bundle.yml run on main of
# this repo. A bundle signed by a fork or a personal key fails.
```

## 5 · Pre-merge checklist

```
☐ All status checks green (test, trivy, gitleaks, checkov, bandit)
☐ CODEOWNERS review obtained
☐ Commit signed (look for "Verified" badge on the commit)
☐ Linear history preserved (no merge commits — squash or rebase)
☐ If touching docs/security/rbac_matrix.md, the matching tests/test_rbac_matrix.py rows are updated in the same PR
☐ If touching infra/terraform/, the resulting plan is reviewed (paste in PR description)
```

## 6 · How an enterprise reviewer can check the chain

```bash
# 1. Inspect that signed commits are required:
gh api repos/Abhi-mishra998/aegis/branches/main/protection \
  | jq '.required_signatures.enabled'    # → true

# 2. Inspect the last shipped bundle's signature:
aws s3 ls s3://aegis-prod-backups-628478946931-v2/releases/   | grep bundle
# Pull the bundle + 3 signature siblings, then run the helper:
bash scripts/ops/verify_signed_bundle.sh /tmp/bundle-<sha>.tar.gz
# The helper pins the cert-identity regex to release_bundle.yml on main —
# bundles signed by any other workflow or branch fail verification.

# 3. Inspect that production refuses unsigned bundles:
aws ssm get-parameter --name /aegis/prod/require_signed_bundle \
  --query Parameter.Value --output text    # → true
```

All three should pass before any enterprise contract closes.
