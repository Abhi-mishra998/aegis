# Public status page — operator setup

Sprint EI-14 (2026-06-20). One-shot setup that wires
`status.aegisagent.in` to the static HTML status mirror in the public
S3 bucket. After this is done the public status page is reachable
*even when the main Aegis ALB is down* — the whole point of a status
page.

## What's already running (no operator action needed)

The nightly_verify workflow publishes three artefacts to the
anonymously-readable bucket `aegis-public-roots-628478946931`:

| Path | Source | Cache TTL |
|---|---|---|
| `nightly/<YYYY-MM-DD>.json` | each nightly run | 1 h |
| `nightly/latest.json`       | overwritten each run | 5 min |
| `uptime/30day.json`         | recomputed each run from per-day archive | 10 min |
| `status/index.html`         | overwritten each run from `ui/public/status-mirror.html` | 5 min |

Both the React `/status` page (`https://aegisagent.in/status`) and the
static HTML mirror read these JSON artefacts client-side.

## Operator one-shot setup (~10 min)

### 1. Enable S3 static website hosting on the bucket

```bash
aws s3 website s3://aegis-public-roots-628478946931 \
  --index-document status/index.html \
  --region ap-south-1
```

This makes the bucket reachable at
`http://aegis-public-roots-628478946931.s3-website.ap-south-1.amazonaws.com/`
(HTTP, not HTTPS — fronted in the next step).

### 2. Issue an ACM certificate for `status.aegisagent.in`

The cert MUST live in `us-east-1` because CloudFront only accepts
us-east-1 certificates. (This is the one piece of infra that's not
co-located with the data-plane.)

```bash
aws acm request-certificate \
  --domain-name 'status.aegisagent.in' \
  --validation-method DNS \
  --region us-east-1
```

Click **Create record in Route 53** in the ACM console to add the
validation CNAME. Cert moves to `ISSUED` in ~5 min.

### 3. Create a CloudFront distribution

Front the S3 website endpoint with CloudFront — gives HTTPS, the
custom domain, and edge caching:

| Setting | Value |
|---|---|
| Origin domain | `aegis-public-roots-628478946931.s3-website.ap-south-1.amazonaws.com` |
| Origin protocol policy | HTTP only (S3 website endpoint is HTTP) |
| Viewer protocol policy | Redirect HTTP → HTTPS |
| Allowed methods | GET, HEAD |
| Cache policy | `CachingOptimized` |
| Alternate domain (CNAME) | `status.aegisagent.in` |
| Custom SSL certificate | the cert from step 2 |
| Default root object | `status/index.html` |

### 4. Point Route 53 at CloudFront

```bash
HOSTED_ZONE_ID=$(aws route53 list-hosted-zones-by-name \
  --dns-name aegisagent.in --query 'HostedZones[0].Id' --output text)
CLOUDFRONT_DOMAIN=<distribution-domain-from-step-3, e.g. d1234abcd.cloudfront.net>

aws route53 change-resource-record-sets \
  --hosted-zone-id "$HOSTED_ZONE_ID" \
  --change-batch '{
    "Changes": [{
      "Action": "UPSERT",
      "ResourceRecordSet": {
        "Name": "status.aegisagent.in",
        "Type": "A",
        "AliasTarget": {
          "HostedZoneId": "Z2FDTNDATAQYW2",
          "DNSName": "'"$CLOUDFRONT_DOMAIN"'",
          "EvaluateTargetHealth": false
        }
      }
    }]
  }'
```

`Z2FDTNDATAQYW2` is the global CloudFront alias hosted zone — fixed
for all distributions.

### 5. Verify

```bash
# 1. DNS resolves to CloudFront
dig +short status.aegisagent.in
# expect: an a.b.c.d IP (CloudFront edge)

# 2. Page loads
curl -sS -o /dev/null -w '%{http_code}\n' https://status.aegisagent.in
# expect: 200

# 3. The page renders the latest verify data
curl -sS https://status.aegisagent.in | grep -F 'aegis-public-roots'
# expect: matches (the static HTML embeds the bucket URL)
```

---

## What about an outage of the main aegisagent.in ALB?

That's the scenario this page exists for. Two layers of survivability:

1. **`https://status.aegisagent.in`** lives on CloudFront + S3 —
   independent of the EC2 ASG, RDS, ElastiCache, and gateway. As long
   as CloudFront + S3 + Route 53 are up (which is AWS's problem, not
   ours), this URL serves.
2. **The data the page reads** also lives in the same anonymously-
   readable S3 bucket. Even if the page itself can't render (browser
   blocked, JS disabled), a customer can run
   `aws s3 cp --no-sign-request s3://aegis-public-roots-628478946931/nightly/latest.json -`
   to get the raw status. The Raw Artefacts section on /status documents this.

## What about cost

- S3: ~$0.02/month for the few KB of nightly artefacts × 30 days.
- CloudFront: ~$0.085/GB egress × negligible traffic ≈ $1-2/month.
- Route 53: $0.50/month per hosted zone (we already pay this for
  aegisagent.in).
- ACM cert: free.

Total recurring: <$3/month for a feature that closes a top-of-funnel
sales objection.

---

## Updates after the initial setup

Updates to the status page HTML go through the normal release flow —
`ui/public/status-mirror.html` is overwritten on the next nightly_verify
run. There's nothing to redeploy manually.

If you change the schema of `nightly/latest.json` (adding a new check),
update BOTH the React `/status` page (`ui/src/pages/StatusPage.jsx`)
AND the static HTML (`ui/public/status-mirror.html`) in the same PR.
The two surfaces drift if not updated together.
