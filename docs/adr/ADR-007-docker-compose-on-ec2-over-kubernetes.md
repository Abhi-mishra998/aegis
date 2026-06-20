# ADR-007: Docker Compose on EC2 over Kubernetes

* Status: Accepted
* Date: 2026-06-20
* Deciders: Abhishek Mishra (founder/CTO)
* Tags: infra, deploy, operability, bus-factor

## Context

Aegis ships ~32 services (gateway, identity, policy, decision, audit,
registry, behavior, insight, autonomy, forensics, OPA sidecar,
Prometheus, Grafana, Alertmanager, …) defined in
`infra/docker-compose.yml` (32 services × the base file) +
`infra/docker-compose.aws.yml` (production overlay: log driver,
restart policies, resource limits, named volumes mapped to EBS).

Every modern engineering reviewer asks the same question when they
see this list: **"Why not Kubernetes?"** This ADR is the durable
answer so the question doesn't recur every time a new contractor or
investor looks at the infra layout.

The forces in play at the time of decision:

- **1-FTE bus factor.** The platform is operated by one engineer. K8s
  introduces an entire control plane (API server, scheduler, etcd,
  ingress controller, CSI drivers, RBAC, NetworkPolicies) that itself
  needs an operator. Even managed K8s (EKS, GKE) leaves the
  add-on-management problem fully on the user.
- **No customer asked for it.** The brutal-review F500-readiness gate
  doesn't list "deployed on Kubernetes" anywhere. CISOs care about the
  *outcomes* (tenant isolation, append-only audit, restart-on-failure)
  not the orchestrator.
- **Predictable blast radius.** Two EC2 ASG instances behind an ALB
  has a known failure mode — one EC2 dies, the ALB drains it, the ASG
  launches a replacement. K8s has many more failure modes (control-
  plane unavailability, etcd corruption, CNI plugin bugs, node
  pressure eviction loops).
- **Deploy story matches the team.** `bash scripts/ops/deploy_staggered.sh`
  is a 60-line shell script the operator can read end-to-end. A k8s
  Helm chart + ArgoCD pipeline is at least 10× more moving parts.

## Decision

Production runs **Docker Compose on EC2**, specifically:

- 2× `m6g.large` instances in an Auto Scaling Group behind an ALB
  (`infra/terraform/modules/asg/main.tf`).
- User-data on boot: pull deploy bundle from S3 + cosign-verify (ADR-
  pending for cosign signing chain; see EI-10) + `docker compose -f
  infra/docker-compose.yml -f infra/docker-compose.aws.yml up -d`.
- ALB health probes the gateway's `/health` on each instance.
- `restart: always` on every compose service handles in-instance
  restarts; ASG instance refresh handles instance-level
  replacements.
- Per-host cron jobs (Prometheus AlertManager + status-page publish)
  rather than a CronJob abstraction.

Staging mirrors the same shape at one EC2 instance
(`infra/terraform/envs/staging/terraform.tfvars.example`).

## Alternatives considered

1. **Amazon EKS** (managed Kubernetes). Rejected because:
    - $73/month EKS control-plane fee × 2 regions = $146/month for
      zero added value the customer can see.
    - At least one full FTE for k8s admin work that the platform
      doesn't have.
    - Adds an order-of-magnitude more failure modes (etcd, CNI,
      ingress controller, CSI, RBAC).
    - The EKS-specific operational knowledge (kubectl, Helm, kustomize,
      Karpenter, CRDs, operators) doesn't generalise to our use case
      — we'd be carrying it for the sake of carrying it.
2. **ECS Fargate.** Better fit than EKS — no control plane, AWS-managed
   scheduler. Rejected because:
    - Per-container billing is meaningfully more expensive than two
      EC2 ASG instances at our scale ($420/mo vs ~$200/mo for the
      compute alone).
    - Fargate task networking is a separate ENI per task — we'd burn
      VPC IP space at 32 services × 2 hosts.
    - No persistent in-host volumes; named volumes via EFS adds a
      moving part.
    - The cost story flips above ~100 customers; revisit when we get
      there.
3. **Lightsail Containers.** Considered for one-VM simplicity.
   Rejected — no ALB integration, no VPC, no Multi-AZ story. Loses
   the production HA shape.
4. **Single bare EC2 with systemd units per service.** What we had
   before docker-compose was introduced. Rejected — re-shipping
   would lose containerisation's "same artefact dev and prod" property
   and re-introduce systemd unit drift between hosts.
5. **AWS App Runner.** Considered for the gateway only. Rejected —
   doesn't fit the multi-service compose topology we need.

## Consequences

* **Positive**
  - 1-FTE operability — the operator can `ssh` to a host and run
    `docker compose ps` to see the entire system in one window.
  - Failure modes are small + well-understood (container crash →
    `restart: always`; host crash → ASG replacement; deploy fail →
    rollback by repointing the SSM bundle SHA).
  - Cost: ~$200/month compute + ~$200/month managed services (RDS,
    Redis, ALB, NAT). Total infra ~$420/month at the current scale.
  - All 32 services share localhost networking on the same host —
    no service-mesh complexity to operate.
  - Deploy is 60 lines of shell; an investor doing technical due
    diligence can read it in 5 minutes.
* **Negative**
  - No horizontal scaling beyond 2 hosts without ASG redesign +
    sticky-session handling for SSE clients. Will hit this ceiling
    around 1k req/s sustained.
  - No declarative service-state language (a K8s manifest IS the
    intended state; compose is start-orchestration only). When
    a service drifts, recovery is "restart it" not "reconcile to
    spec."
  - In-host docker-daemon failure brings down all 32 services on
    that host. Mitigated by the 2-host ASG; aggravated when a
    docker upgrade goes wrong (low frequency, handled by manual
    `docker version` pinning at the AMI level).
* **Reversibility**
  - **Migration to K8s is non-trivial but mechanical** when the
    business outgrows the current shape. The docker-compose.yml is
    structurally close to a Helm chart (named services, dependencies,
    env, volumes); a one-time `kompose convert` produces a workable
    starting point. Estimate: 4-6 weeks of focused work + 1 FTE on
    k8s ops thereafter.

## Implementation references

* `infra/docker-compose.yml` — 32 service definitions, base file
* `infra/docker-compose.aws.yml` — production overlay (log driver,
  resource limits, named volumes)
* `infra/terraform/modules/asg/main.tf` — ASG + launch template +
  user-data
* `scripts/ops/deploy_staggered.sh` — 60-line staggered ALB deploy
* `infra/terraform/envs/staging/terraform.tfvars.example` — staging
  uses the same compose layout at 1× t4g.small
* `docs/runbooks/disaster_recovery.md` §3 — recovery procedure
  matches the compose topology

## Verification

```bash
# 1. Sanity-check the compose service count matches what the ADR claims.
grep -cE '^  [a-z_-]+:' infra/docker-compose.yml
# expect: 32 (drift in either direction means update this ADR)

# 2. Confirm production runs from this exact compose layout.
ssh -i $KEY ec2-user@$HOST 'sudo docker compose -f /opt/aegis/infra/docker-compose.yml \
                                              -f /opt/aegis/infra/docker-compose.aws.yml \
                                              ps --format json' \
  | jq -s 'length'
# expect: matches the count above

# 3. Confirm no Kubernetes / Helm / ArgoCD references exist anywhere.
grep -rln -E "(apiVersion: apps/|^kind: Deployment|^Chart\.yaml)" infra/ services/ 2>/dev/null
# expect: no output
```
