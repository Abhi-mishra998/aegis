# Aegis Helm chart — for self-host customers only

## Intent (declared sprint-3.6)

This Helm chart is **for self-host customers** who want to run Aegis on their
own Kubernetes cluster. It is **NOT** what `aegisagent.in` itself runs.

Production at `aegisagent.in` runs `docker compose up -d` on two EC2 hosts
behind an ALB (see `.github/workflows/deploy.yml`). The Helm chart is
maintained as a parallel deployment artifact so customers who already have
EKS / GKE / AKS / OpenShift do not have to translate the compose file
themselves.

## Why both?

| Concern | docker-compose (aegisagent.in) | helm/acp (self-host) |
|---|---|---|
| Deploy target | 2× EC2 + ALB | Kubernetes cluster |
| Ops burden | Minimal (single host) | Cluster-native scaling, rolling updates, HPA |
| Image source | Built on host from source | Pull from ECR / customer registry |
| Secret management | `infra/.env` (gitignored) | Helm Secrets / Sealed Secrets / External Secrets |
| Observability | Prometheus + Grafana + Jaeger containers | Customer's existing K8s observability stack |

Keeping both is intentional. A migration of aegisagent.in to Kubernetes is
**not on the sprint-3/4 roadmap.** When it lands it will be a sprint of its
own.

## CI validation

`helm lint` runs in `.github/workflows/test.yml` on every PR that touches
`infra/helm/**`. A failing lint blocks merge. This catches template-syntax
regressions without requiring a full cluster to test.

## How to deploy (self-host)

```bash
# 1. Render values for your environment
cp infra/helm/acp/values.yaml infra/helm/acp/values.local.yaml
# Edit ingress hostname, replica counts, image tag.

# 2. Create the namespace
kubectl create namespace aegis

# 3. Install
helm install aegis infra/helm/acp/ \
  -n aegis \
  -f infra/helm/acp/values.local.yaml

# 4. Wait for pods
kubectl -n aegis get pods -w
```

## Updating the chart

Bump `version:` in `infra/helm/acp/Chart.yaml` for every change. SemVer:
- patch: bug fixes, template tweaks
- minor: new values, new optional resources
- major: breaking values changes (rename fields, change required keys)

The chart's `appVersion:` should match the corresponding `acp` package
version in `pyproject.toml` at release time.
