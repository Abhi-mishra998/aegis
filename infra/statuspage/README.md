# Aegis customer status page — sprint-6.4

Static single-file renderer that consumes the JSON snapshots from
`scripts/maintenance/publish_status_page.py` (sprint-4.G).

## What's here

- `index.html` — vanilla HTML/CSS/JS, no framework, no build step. Polls
  `/status/current.json` every 30s and renders the overall pill, per-service
  status grid, 24h availability + 1h audit-row counters.

## Deploy

1. Create the bucket Terraform already declares: `s3://aegis-statuspage` (see `infra/terraform/s3.tf`).
2. Upload `index.html` + a copy of `infra/statuspage/index.html` (or replace below):
   ```bash
   aws s3 cp infra/statuspage/index.html s3://aegis-statuspage/index.html \
     --content-type text/html --cache-control "max-age=60"
   ```
3. Configure the bucket for static-website hosting (Terraform stub does
   this via `aws_s3_bucket.statuspage`; if not yet imported, do it in the console).
4. Optionally front the bucket with a CloudFront distribution + the
   `status.aegisagent.in` certificate so the page is HTTPS.
5. Confirm `https://status.aegisagent.in/` renders within 5 seconds of the
   next `publish_status_page.py` cron run.

## How the JSON looks

```json
{
  "version": 1,
  "generated_at": "2026-05-29T09:51:15.808006+00:00",
  "overall_status": "operational",
  "uptime_24h": 0.9995,
  "audit_rows_1h": 14,
  "services": [
    {"name": "gateway",   "status": "operational", "latency_ms": 21},
    {"name": "audit",     "status": "operational", "latency_ms": 18},
    {"name": "decision",  "status": "operational", "latency_ms": 32},
    {"name": "identity",  "status": "operational", "latency_ms": 14}
  ]
}
```

## Security considerations

- The page renders ONLY service-level signals and rolled-up SLI numbers.
  No tenant data is ever included in the JSON — `publish_status_page.py`
  uses Prometheus aggregates and global health probes only.
- No customer JWT is required; the page is intentionally public.
- The poll URL is hardcoded to `/status/current.json` (same origin). If
  the page and the JSON are on different origins, set `BASE_URL` in the
  script section to the JSON origin and ensure CORS allows it.

## Tooling parity

If you'd rather pay Statuspage.io or Better Stack Status, this page is
disposable — the JSON snapshot is the actual contract. Replace `index.html`
with their dashboard import; keep `publish_status_page.py` so the
underlying data still works.
