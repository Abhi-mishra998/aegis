# GitHub Actions → AWS OIDC role

Sprint EI-4 (2026-06-20). The nightly-soak + nightly-verify workflows
authenticate to AWS via OpenID Connect — no long-lived access keys
land in GitHub Secrets, no per-key rotation cron job.

## The role

```hcl
# infra/terraform/modules/iam/github_oidc.tf — operator-applied once.
resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]  # GH's well-known
}

resource "aws_iam_role" "gha_nightly" {
  name = "aegis-gha-nightly-soak"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        # Scope to THIS repo's main branch and the nightly_* workflows
        # only — a leaked workflow from a fork can't assume this role.
        StringLike = {
          "token.actions.githubusercontent.com:sub" =
            "repo:Abhi-mishra998/aegis:ref:refs/heads/main"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "gha_nightly_perms" {
  role = aws_iam_role.gha_nightly.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ssm:GetParameter", "ssm:PutParameter",
          "ssm:SendCommand",  "ssm:GetCommandInvocation",
        ]
        Resource = [
          "arn:aws:ssm:ap-south-1:628478946931:parameter/aegis/staging/*",
          "arn:aws:ssm:ap-south-1:628478946931:document/AWS-RunShellScript",
        ]
      },
      {
        Effect = "Allow"
        Action = ["autoscaling:StartInstanceRefresh",
                  "autoscaling:DescribeAutoScalingGroups",
                  "autoscaling:DescribeInstanceRefreshes"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = ["s3:PutObject", "s3:GetObject", "s3:ListBucket"]
        Resource = [
          "arn:aws:s3:::aegis-staging-backups-628478946931",
          "arn:aws:s3:::aegis-staging-backups-628478946931/*",
          # Read-only for the public roots bucket — verifier needs to
          # walk historical roots.
          "arn:aws:s3:::aegis-public-roots-628478946931",
          "arn:aws:s3:::aegis-public-roots-628478946931/*",
        ]
      },
      {
        Effect = "Allow"
        Action = "elasticloadbalancing:DescribeTarget*"
        Resource = "*"
      },
    ]
  })
}

output "gha_nightly_role_arn" {
  value = aws_iam_role.gha_nightly.arn
}
```

## Wiring it to the workflow

```yaml
permissions:
  id-token: write   # required for OIDC
  contents: read

jobs:
  soak:
    runs-on: ubuntu-latest
    steps:
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::628478946931:role/aegis-gha-nightly-soak
          aws-region: ap-south-1
```

No `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` ever touch GitHub.

## One-time bootstrap (operator)

```bash
cd infra/terraform
# Add the github_oidc.tf shown above to modules/iam, then:
terraform apply -var-file=envs/prod/terraform.tfvars
terraform output gha_nightly_role_arn
# Paste the ARN into .github/workflows/nightly_soak.yml in place of
# the placeholder ROLE_ARN line.
```

## What this role CANNOT do

- Touch any `/aegis/prod/*` SSM parameter.
- Run SSM commands against prod EC2s (the IAM scope above is restricted
  to the staging SSM parameter prefix, but SendCommand IAM in AWS is
  shaped by document + instance tag, NOT prefix — so the workflow's
  send-command call MUST also condition on the `Environment=staging`
  instance tag at the document policy layer).
- Write to the public transparency bucket (read-only).
- Assume any other role.

This is intentional — a compromised nightly workflow can poke staging
into a known-bad state but cannot exfiltrate prod data or harm prod
infrastructure.
