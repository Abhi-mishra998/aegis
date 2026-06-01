# Aegis Terraform

Production state at `aegisagent.in` was originally created via the AWS
console. This module set captures the live state, declares it as code,
and supplies a downscaled **`dev`** environment sized for a 10-user
test portal at < ~$50 / month.

## Layout

```
infra/terraform/
├── bootstrap/                   # one-time: creates the S3 backend bucket
│                                #            + DynamoDB lock table
├── modules/                     # reusable building blocks (one concern each)
│   ├── network/                 # VPC, subnets, IGW, route tables
│   ├── security_groups/         # ALB, EC2, RDS, Redis SGs
│   ├── iam/                     # EC2 instance role + policies
│   ├── compute/                 # EC2 instances (count + type + AZ-mapped subnets)
│   ├── rds/                     # PostgreSQL Multi-AZ-capable
│   ├── elasticache/             # Redis replication group
│   ├── alb/                     # ALB + listeners + target group
│   ├── s3/                      # backups + statuspage + ALB-logs buckets
│   ├── acm/                     # certificate + DNS validation records
│   ├── route53/                 # apex + subdomain alias records
│   └── secrets/                 # Secrets Manager entries (DB password, JWT, ...)
└── environments/
    ├── prod/                    # mirrors the actual live deployment
    └── dev/                     # downscaled — 1 EC2 t3.small, single-AZ RDS,
                                 # 1× cache.t3.micro, no ElastiCache replica
```

## Quickstart

### 1. Bootstrap the state backend (once per account)

```bash
cd infra/terraform/bootstrap
terraform init
terraform apply        # creates s3://aegis-terraform-state + DDB lock table
```

### 2. Initialize an environment

```bash
cd infra/terraform/environments/dev    # or environments/prod
terraform init
terraform plan
terraform apply
```

### 3. Import existing live resources into `prod` (one-time)

See [`environments/prod/IMPORT.md`](environments/prod/IMPORT.md). Each
existing resource is mapped to a `terraform import` command that brings
the live ID under management without recreating it.

## Budget envelope

The `dev` environment is sized for a 10-user test portal:

| Resource | dev (10 users) | prod (current live) | dev monthly cost (ap-south-1) |
|----------|----------------|---------------------|-------------------------------|
| EC2      | 1× t3.small, single AZ | 2× t3.2xlarge, 2 AZs | ~$15 |
| RDS      | db.t4g.micro, single AZ, 20 GB gp3 | db.t3.micro Multi-AZ, 20 GB gp3 | ~$13 |
| Redis    | cache.t3.micro, 1 node | cache.t3.micro, 1 node | ~$11 |
| ALB      | 1 | 1 | ~$16 (fixed) |
| Data tx  | <1 GB/month assumed | n/a | ~$0 |
| **Total** |  |  | **~$55 / month** |

Hard ceiling: a budget alert at $60 / month fires via SNS → email
(`modules/secrets` writes a placeholder; operator sets the real address).

To stay under $50, drop the ALB and expose the gateway directly on the
EC2 public IP via a CloudFront distribution instead. See
`environments/dev/README.md` § "Even cheaper".

## Conventions

- **One module per concern.** No module declares resources outside its
  domain. Module boundaries match the AWS team that owns each.
- **Modules take inputs, return outputs.** No module reads `data` sources
  for cross-module state — that's the environment's job (composition).
- **`required_version`** pinned to `>= 1.6.0` everywhere.
- **`required_providers`** pinned to a specific minor (currently `5.4x`).
- **Tags** are environment-prefixed (`acp-dev-*`, `acp-prod-*`) so AWS
  Cost Explorer can split spend per environment.
- **State** lives in `s3://aegis-terraform-state/{env}/terraform.tfstate`
  with DynamoDB lock at `aegis-terraform-locks`.

## Discovered live state (snapshot from `aws describe-*` at sprint-8 time)

| Resource | ID / name |
|----------|-----------|
| VPC | `vpc-0b86b702b936fc905` (`10.0.0.0/16`) |
| Public subnets | `subnet-00ce70dbbbe9602f1` (1a), `subnet-0b808f72efc46dff2` (1b) |
| Private subnets | `subnet-01baf8689d58a521d` (1a), `subnet-0a32990f24a17d8a2` (1b) |
| ALB | `acp-alb` (DNS: `acp-alb-357872136.ap-south-1.elb.amazonaws.com`) |
| Target group | `acp-ui-tg` (port 5173, /health) |
| EC2 | 2× `acp-server-prod` (t3.2xlarge), 1 per AZ |
| RDS | `acp-postgres-prod` (db.t3.micro, Multi-AZ, gp3, postgres 15.18) |
| Redis | `acp-redis-prod` (cache.t3.micro, single node, redis 7.1) |
| Route53 | `Z033117538JKIIKDBDPUJ` (`aegisagent.in`) |
| ACM | `74f2f769-...` (aegisagent.in + api + www) |
| IAM role | `acp-ec2-role` (SSM, CloudWatchAgent, S3FullAccess) |
| S3 buckets | `acp-backups-abhishek-prod`, `acp-backups-prod-am`, `acp-fix-1779860735` |
| EC2 SG | `sg-0e8b5bdd4d4a0d9b0` (`acp-ec2-sg`) |
| ALB SG | `sg-0c50d69ba3de40bf3` (`acp-alb-sg`) |
| RDS SG | `sg-0e72625fc48706b98` (`acp-rds-sg`) |
| Redis SG | `sg-00e47aee22e90ae33` (`acp-redis-sg`) |

The prod environment mirrors this exactly so `terraform import` reproduces
the live state without drift.
