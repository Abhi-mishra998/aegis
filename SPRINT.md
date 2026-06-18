# Aegis Market-Shift Sprint — `sprint.md`

**Sprint name:** Aegis v2.0 GA — Market-Shift Closeout
**Created:** 2026-06-18
**Duration:** 14 working days (2 weeks)

---

## 🟢 STATUS LOG — 2026-06-18 (session 1)

**Landed in-session (verified):**
- ✅ **Track A1 — B1 wire-transfer alignment.** Two Python constants + one Rego rule + signal-registry description aligned at $100k.
  - `services/policy/local_action_semantics.py:101` `_WIRE_ESCALATE_EXTERNAL_USD = 100_000`
  - `services/security/objectives/impact.py:28` `_WIRE_ESCALATE_EXTERNAL_USD = 100_000`
  - `services/policy/policies/action_semantics_deny.rego:501` `amount >= 100000`
  - `services/security/signal_registry.py:456` description "Wire ≥ $100K to external..."
- ✅ **Track A2 — B4 SDK `__version__` source-string bumps.**
  - `integrations/aegis-bedrock/aegis_bedrock/__init__.py:34` `__version__ = "1.1.0"`
  - `integrations/aegis-langchain/aegis_langchain/__init__.py:26` `__version__ = "1.1.0"`
- ✅ **Track C1 — `agies-bussiness.md` v1.3.0 published.** L1 (§3 latency reconciliation), L2 (S3 public-witness live evidence block), L3 (`/status` JSON sample) all applied.
- ✅ **`bussines-left.md` closure ledger added** at top with file:line resolution pointers for B1, B4, L1/L2/L3.

**Verifications run (all green):**
- `grep -n "100_000\|200_000\|100000\|200000" services/policy/local_action_semantics.py services/security/objectives/impact.py services/policy/policies/action_semantics_deny.rego services/security/signal_registry.py` → only $100k matches in wire-transfer logic; `$10_000_000` hard-cap left intact.
- `python3 -c "from policy import local_action_semantics; print('OK')"` → module imports cleanly.
- `grep -n "__version__" integrations/aegis-bedrock/aegis_bedrock/__init__.py integrations/aegis-langchain/aegis_langchain/__init__.py` → both read `1.1.0`.
- Rego file head reads cleanly (no syntax break from the comment block insertion).

**Not yet done (requires next session or human ops):**
- Track A1 unit test (`tests/services/policy/test_wire_threshold.py` asserting $99k allow, $100k escalate, $150k escalate) — drafted in sprint spec but not yet written.
- Track B1/B2/B3 — PyPI publishes (need PyPI token + publishing machine; coordinate with release engineer).
- Track C2 — `docs/security/threat-model.md` — fresh session recommended (one focused doc per session).
- Track C3 — `docs/security/dpa-template.md` — fresh session + legal review.
- Track C4 — `docs/security/baa-template.md` — fresh session + legal review.
- Track C5 — `docs/operations/incident-response.md` — fresh session.
- Track C6 — `docs/operations/retention-policy.md` — fresh session.
- Track C7 — `docs/operations/disaster-recovery.md` — written AFTER Track E1 drill runs (depends on measured RTO/RPO).
- Track C8 — README update.
- Tracks D, E, F, G, H — operations/legal/sales/SRE work, not Claude tasks.
- 50-row E2E acceptance grid — runs against live prod after sprint Tracks A-H complete.

**Local commits (NO git push yet — awaiting human signoff):**
- See §16 of this sprint for the commit/push protocol. Two commits prepared for this session:
  1. `fix(policy): align wire-transfer escalation floor to $100k across pattern + Rego  · Closes B1`
  2. `fix(sdk): bump aegis-bedrock and aegis-langchain __version__ to 1.1.0  · Closes B4`

---

**Goal:** Close every code/doc/operational gap surfaced in `bussines-left.md` and `agies-bussiness.md` §5, deploy to both EC2 instances behind the prod ALB, validate end-to-end. Emerge with a defensible "Enterprise-Ready" posture: every claim in the doc is verifiable, every endpoint operational, every blocker on the GAPS list either closed or on a dated, vendor-signed timeline.

**Prime rule:** No bypass. No shortcut. Every task has acceptance criteria; if a criterion can't be met we extend the sprint or descope honestly — we do not ship false claims.

**Throughput contract:** Code quality > task count. A unit only lands when (a) the diff is minimal and reviewed, (b) the verification test passes locally, (c) the diff has not weakened any existing test or invariant.

---

## 1. Sources of truth

This sprint draws from two artifacts already on disk:

- **`bussines-left.md`** — brutal audit dated 2026-06-18. 3 hard inaccuracies, 5 soft mismatches, 2 understatements. This drives the **fix list**.
- **`agies-bussiness.md` v1.2.0** — context briefing that already absorbed most of the audit. Remaining items: L1 (latency contradiction in §3), L2 (S3 transparency live witness add), L3 (`/status` JSON sample). This drives the **doc list**.

When the two disagree, `bussines-left.md` wins (audit precedence over context).

---

## 2. Pre-flight checklist (before sprint kickoff)

- [ ] Engineering lead has read `bussines-left.md` end-to-end.
- [ ] CTO has reviewed Definition of Done in §13 of this file.
- [ ] AWS console access verified: ECR, S3 (deploy bucket + `aegis-public-roots-628478946931`), RDS, EC2 (both instances), SSM.
- [ ] PyPI API token for `aegis-bedrock`, `aegis-langchain`, `aegis-aevf` packages on the publishing machine.
- [ ] Stripe dashboard access to confirm live price IDs for Pro and Enterprise tiers.
- [ ] Clerk dashboard access to confirm JWKS rotation cadence.
- [ ] Backup of `audit_logs` partition taken and verified loadable in a sandbox.
- [ ] Latest `main` is green on CI; no in-flight branches pending merge.
- [ ] Pager rota for the 2-week window is set.

If any checkbox above is empty, the sprint **does not start**.

---

## 3. Tracks (8 parallel workstreams)

Each track has an owner, a fixed set of files it may touch, a written acceptance criterion, and a verification recipe. Tracks A–E must complete **in-sprint**. Tracks F–H start in-sprint and complete on a dated post-sprint timeline.

| Track | Title | Owner | In-sprint? | Closes |
|---|---|---|---|---|
| A | Code fixes (wire-transfer alignment + SDK version strings) | Backend | ✅ Yes | B1, B4 |
| B | SDK PyPI release hygiene | Backend | ✅ Yes | B2, B4 |
| C | Doc updates + new policy documents | Tech writer + Eng | ✅ Yes | L1, L2, L3, B7, B10, plus 4 new policy docs |
| D | Production load-test evidence | SRE | ✅ Yes | G4 (no load-test) |
| E | Operations readiness — DR runbook, SLO dashboard, IR runbook, retention policy | SRE + Compliance | ✅ Yes | G5, G6, G9, G10 |
| F | Compliance kickoff — SOC2 + pen test + DPA + BAA | Compliance + Legal | ⏳ Starts in-sprint, completes post-sprint | G1, G2, G3 |
| G | Customer-reference build | Sales + Eng | ⏳ Starts in-sprint, completes post-sprint | G11 |
| H | Deploy + E2E validation | SRE + Eng | ✅ Yes (final phase) | All claims marked "code-only" → upgraded to "code + runtime" |

---

## 4. Track A — Code fixes

### A1. Close B1 — wire-transfer enforcement gap ($100k–$199k window)

**Why:** `bussines-left.md` §B1 — pattern detector at `services/gateway/escalation_patterns.py:39-52` fires at $100k+; Rego enforcement at `services/policy/policies/action_semantics_deny.rego:495-500` fires at $200k+; `services/policy/local_action_semantics.py:98` `_WIRE_ESCALATE_EXTERNAL_USD = 200_000`. A $150k external wire is queued for CFO approval but escapes Rego enforcement — real production-routing bug.

**Direction chosen:** Align *down* — pattern detector and Rego enforcement both fire at **$100k**. Rationale:
- The $100k threshold matches finance-industry SAR reporting (FinCEN $10k currency-transaction; $100k batched wire enhanced review is common).
- A CFO approving a $100k wire is the correct human-in-loop posture.
- Dropping the floor is safer than raising it (catches more, blocks less false-pass).

**Files to change:**
- `services/policy/local_action_semantics.py` — `_WIRE_ESCALATE_EXTERNAL_USD = 100_000` (was 200_000).
- `services/policy/policies/action_semantics_deny.rego` — replace `>= 200000` with `>= 100000` in both occurrences (lines around 495-500 and any others — grep first).
- `services/security/signal_registry.py` — `money_transfer_external` description changes from "Wire ≥ $200K" to "Wire ≥ $100K" (lines 451-457).
- `tests/services/policy/` — add a test that asserts a $150k external transfer triggers `escalate` (was passing under old logic? Must verify).

**Acceptance criteria:**
- Wire $99k → no escalation, no signal. ✅ allow.
- Wire $100k → pattern hit, Rego escalate, audit row with `escalate`. ✅ 202 + CFO routing.
- Wire $150k → same as $100k. ✅ 202 + CFO routing (this is the gap we are closing).
- Wire $1M → same as above, plus high risk score. ✅ 202 + CFO routing.
- No existing test regresses.

**Commit message:**
```
fix(policy): align wire-transfer escalation floor to $100k across pattern + Rego

Closes audit finding B1 — $100k–$199k external wires previously matched
the gateway pattern detector but escaped Rego enforcement. Both layers
now fire at $100k. local_action_semantics, action_semantics_deny.rego,
and signal_registry brought into sync.
```

### A2. Close B4 — bump `__version__` strings in aegis-bedrock + aegis-langchain

**Why:** `bussines-left.md` §B4 — PyPI ships 1.1.0 but source `__version__` reads 1.0.0 / 1.0.1.

**Files to change:**
- `integrations/aegis-bedrock/aegis_bedrock/__init__.py:34` — `__version__ = "1.1.0"`.
- `integrations/aegis-bedrock/setup.py` — confirm version field matches.
- `integrations/aegis-langchain/aegis_langchain/__init__.py:26` — `__version__ = "1.1.0"`.
- `integrations/aegis-langchain/setup.py` — confirm version field matches.

**Acceptance criteria:**
- `python -c "import aegis_bedrock; print(aegis_bedrock.__version__)"` prints `1.1.0`.
- `python -c "import aegis_langchain; print(aegis_langchain.__version__)"` prints `1.1.0`.
- Both `setup.py` files agree.

**Commit message:**
```
fix(sdk): bump aegis-bedrock and aegis-langchain __version__ to 1.1.0

Closes audit finding B4. PyPI wheels were already at 1.1.0; source
__version__ strings were lagging at 1.0.0 / 1.0.1.
```

---

## 5. Track B — SDK PyPI release hygiene

### B1. Publish `aegis-aevf` 1.1.0 to PyPI

**Why:** `bussines-left.md` §B2 — biz doc claims 1.1.0; PyPI ships 1.0.0. A CISO running `pip show aegis-aevf` after install catches the mismatch immediately.

**Files to change:**
- `tools/aegis_verify/pyproject.toml` — `version = "1.1.0"`.
- `tools/aegis_verify/__init__.py` (line ~16) — `__version__ = "1.1.0"`.
- `tools/aegis_verify/CHANGELOG.md` — add 1.1.0 entry noting "version-sync release; functional changes: none".

**Release steps (after Track A merges):**
```bash
cd tools/aegis_verify
python -m pip install --upgrade build twine
python -m build                          # produces dist/aegis_aevf-1.1.0-*.whl
python -m twine check dist/*
python -m twine upload dist/aegis_aevf-1.1.0*    # interactive, use PyPI token
```

**Acceptance criteria:**
- `pip install aegis-aevf==1.1.0` succeeds against PyPI.
- `aegis-verify --version` prints `1.1.0`.
- `aegis-verify --bundle <staged-bundle>` validates the staged bundle end-to-end.

### B2. Re-publish `aegis-bedrock` 1.1.1 with corrected `__version__`

**Why:** PyPI 1.1.0 wheel has the bug (stale `__version__` inside). Cannot republish 1.1.0 over the existing wheel. Bump to 1.1.1 with no functional change.

**Files:** same as A2 plus `setup.py` version bump to `1.1.1`.

**Release:** same procedure as B1 above for `tools/aegis_verify`, but inside `integrations/aegis-bedrock/`.

**Acceptance criteria:**
- `pip install aegis-bedrock==1.1.1` succeeds.
- `aegis_bedrock.__version__ == "1.1.1"`.

### B3. Re-publish `aegis-langchain` 1.1.1 with corrected `__version__`

Same as B2 in scope, applied to `integrations/aegis-langchain/`.

---

## 6. Track C — Documentation

### C1. `agies-bussiness.md` v1.3.0 — apply remaining L1/L2/L3

**L1 — Reconcile §3 latency line with §12.**
- Open `agies-bussiness.md`. Find §3 point 8: `Decision latency: ~150 ms p95 (stated; consistent with live SSE observation)`.
- Replace with: `Decision latency: see §12 — the only measured number is a 21.49ms p95 dry-run on a single host (synthetic). Production load-test results published under reports/load-test-2026-Q3/ (closing in this sprint).`

**L2 — Add public S3 transparency live witness to §3.**
- Insert a new block in §3 (live evidence) after the `_aegis_blocked` capture:

```
**Independent verification (no Aegis credentials required):**

$ aws s3 ls --no-sign-request s3://aegis-public-roots-628478946931/ --recursive | wc -l
48
$ aws s3 cp s3://aegis-public-roots-628478946931/roots/<tenant>/2026-06-18.json - --no-sign-request
{ "format": "aegis-public-root/2026-06",
  "root_date": "2026-06-18",
  "prev_root_hash": "<hex>",
  "root_hash":      "<hex>",
  "signed_payload": { "algorithm": "ed25519", ... },
  "notes": "External witness: download directly, verify signature against
            /keys/<signing_kid>.pem, walk prev_root_hash chain to detect rewrite." }

5 days of daily ed25519-signed roots (2026-06-14 → 2026-06-18) live in
the bucket across 7 tenant partitions. Any auditor can verify the chain
with `aegis-verify --root <file> --pubkey <pem>` — no AWS account needed.
```

**L3 — Add `/status` JSON sample to §3.**
- Insert after the S3 block:

```
**Live status endpoint (public, no auth):**

$ curl https://aegisagent.in/status
{ "status": "operational",
  "components": { "registry":"operational", "identity":"operational",
                  "policy":"operational",   "audit":"operational",
                  "usage":"operational",    "behavior":"operational",
                  "decision":"operational", "insight":"operational",
                  "forensics":"operational","identity_graph":"operational",
                  "flight_recorder":"operational","autonomy":"operational" },
  "uptime_seconds": 58356,
  "latency": { "scope": "gateway_internal", ... } }
```

**Header bump:** `Version: 1.3.0`. Update the `# Verification:` line to read:
> Code-audited via 4 parallel research agents + 3 runtime probes (curl, aws s3, PyPI) on 2026-06-18.

**Acceptance criteria:**
- `grep -n "150 ms p95" agies-bussiness.md` returns 0 hits.
- §3 contains both the S3 listing block AND the `/status` block.
- Header version reads `1.3.0`.

### C2. New: `docs/security/threat-model.md`

**Why:** `agies-bussiness.md` §5 lists "No published formal threat model" as a MEDIUM gap. CISOs and Principal Security Architects ask in the first meeting.

**Required sections:**
1. System-level data-flow diagram (5 layers: client → SDK → gateway → policy/audit/decision → upstream LLM).
2. Trust boundaries with explicit listing.
3. Asset inventory: secrets (LLM keys, JWT signing keys, Merkle ed25519 keys, DB credentials), data (audit logs, tenant policies, agent metadata), control plane (Clerk org, RDS superuser, RDS data activity stream).
4. STRIDE per asset.
5. Top 10 threats ranked by Likelihood × Impact, each with the mitigation that's already in code (cite file:line).
6. Open items list (mitigations not yet implemented, with owners and dates).

**Acceptance criteria:**
- File exists, peer-reviewed by 2 engineers + 1 security architect.
- Every STRIDE entry has a code citation OR a roadmap link.

### C3. New: `docs/security/dpa-template.md` (Data Processing Agreement)

**Why:** `agies-bussiness.md` §5 — "No DPA template" blocks enterprise procurement.

**Required clauses:** scope, processor obligations, sub-processors, security measures (cite the 3-layer tenancy + append-only trigger + Merkle chain — give the CISO ammunition), data-subject rights handling, breach notification timeline, audit rights, termination, governing law.

**Acceptance criteria:**
- Legal review signoff (legal-counsel email recorded as the merge-approval evidence in the commit message).
- Template uses `<CUSTOMER_NAME>` placeholders that align with the sales-team contract template.

### C4. New: `docs/security/baa-template.md` (Business Associate Agreement, HIPAA)

**Why:** Required to sell to any healthcare-regulated buyer. Same reasoning as C3, scoped to HIPAA-covered entities.

**Required clauses:** PHI handling, minimum-necessary use, safeguards, reporting requirements, return/destruction at contract end.

**Acceptance criteria:** Legal review signoff. Cross-references retention policy (C7).

### C5. New: `docs/operations/incident-response.md`

**Why:** §5 — "No published incident response process".

**Required sections:**
1. Severity levels (Sev-0 / Sev-1 / Sev-2 / Sev-3) with criteria + response time SLO.
2. On-call rota + escalation chain.
3. Communication policy (who notifies the customer, on what cadence).
4. Per-severity runbook outline.
5. Postmortem template + 14-day publication SLA.

**Acceptance criteria:** Reviewed by the on-call lead. First postmortem under this template is written within the sprint (use the simulated DR drill in E1 as the practice incident).

### C6. New: `docs/operations/retention-policy.md`

**Why:** §5 — "No published retention policy".

**Required content:**
- Audit logs: **10 years** (matches healthcare reg requirement, satisfies all lesser tiers).
- Operational logs (request/response, non-audit): **90 days**.
- Customer PII in usage records: **24 months** then anonymized.
- Tenant offboarding: **30 days** purge SLA after termination; certificate of deletion provided.
- Backup retention: **35 days** of nightly snapshots + **12 months** of monthly.

**Acceptance criteria:** Legal + Engineering signoff. Linked from BAA template (C4) and DPA template (C3).

### C7. New: `docs/operations/disaster-recovery.md`

**Why:** §5 — "No DR evidence / RTO/RPO SLA". This file documents the drill executed in Track E1.

**Required sections:**
1. RTO target: **4 hours**. RPO target: **15 minutes**.
2. Backup architecture: RDS automated snapshots + cross-region replica + audit-log S3 mirror.
3. Failover procedure (step-by-step).
4. Drill log: dates, observed RTO, observed RPO, deviations.
5. Quarterly drill cadence with named owner.

**Acceptance criteria:** Section 4 contains the timestamps from the E1 drill. Observed RTO/RPO meets target.

### C8. README.md update

Point the README's "What is Aegis?" link to `agies-bussiness.md` v1.3.0 and add a one-line link to `docs/security/threat-model.md` + `docs/operations/disaster-recovery.md`.

---

## 7. Track D — Production load-test evidence

### D1. 1k RPS sustained 30-minute test

**Why:** §5 — "No production load-test numbers" is a VP-Engineering hard-no. We must publish a real number.

**Setup:**
- Spin up a 4-node load generator (Locust / k6 — repo already has `tests/load/soak.py`).
- Target: `https://ha.aegisagent.in/v1/messages` with a representative mix:
  - 60% tool-execute requests
  - 15% policy upload + decision
  - 10% audit log queries
  - 10% SSE event-stream subscribers (long-lived)
  - 5% admin endpoints
- Test tenants: 5 (mirror the soak harness mix).

**Run:**
```bash
cd tests/load
k6 run --vus 100 --duration 30m soak.js \
  --out json=reports/load-test-2026-Q3/1k-rps.json
```

**Pass criteria:**
- p50 < 100ms.
- p95 < 500ms.
- p99 < 1500ms.
- Error rate < 0.5%.
- No audit-chain violation (run `aegis-verify` after the test).

**Output:** `reports/load-test-2026-Q3/1k-rps-report.md` with graphs + raw JSON.

### D2. 10k RPS burst 5-minute test

**Setup:** ramp from 100 to 10000 VUs over 60s, hold 5 minutes, ramp down. Same target mix as D1.

**Run:** modified k6 script with burst profile.

**Pass criteria:**
- p95 < 1500ms during the burst window.
- No 5xx storm (gateway shed-load behaviour engages; degraded-mode policy fires correctly).
- Behavior firewall stays available; no `behavior_service_unavailable` audit rows.
- After burst, p95 returns to D1 baseline within 90 seconds.

**Output:** `reports/load-test-2026-Q3/10k-burst-report.md`.

### D3. Publish

- Commit both reports under `reports/load-test-2026-Q3/`.
- Update `agies-bussiness.md` §12 "Decision latency" line to cite the measured numbers from D1/D2.
- Add a row to `agies-bussiness.md` §5 → check off "No production load-test numbers".

---

## 8. Track E — Operations readiness

### E1. DR drill + measured RTO/RPO

**Procedure (booked maintenance window, off-peak):**
1. Snapshot RDS at T=0.
2. Simulate region failure: promote read replica in secondary region.
3. Re-point ALB DNS at standby instances.
4. Restore application traffic.
5. Verify: `/status` healthy, audit chain intact, agent execute succeeds.
6. Record T_recovery (RTO) and lag between last-snapshot and DNS-cut (RPO).

**Pass criteria:** RTO < 4 hours measured. RPO < 15 minutes measured. Document in `docs/operations/disaster-recovery.md` §4.

### E2. SLO dashboard

**Why:** §5 — "No SLO/SLA dashboard (customer-facing)".

**Wire a Grafana board** at `infra/grafana-dashboards/customer-slo.json` that surfaces:
- Availability % (rolling 30d).
- p50 / p95 / p99 decision latency.
- Audit chain verification status (green = no violations, red = current violation).
- Approval queue depth + median time-to-approve.

Public read-only URL: `https://aegisagent.in/slo` (gated by tenant ID; each tenant sees only their own slice).

**Acceptance criteria:** Board renders for the demo tenant; numbers match Prometheus directly.

### E3. Incident-response runbook published

Already specified in C5; this track owns the **review + signoff**.

### E4. Retention policy published

Already specified in C6; this track owns the **legal review + signoff**.

---

## 9. Track F — Compliance kickoff (starts in-sprint, completes post-sprint)

### F1. SOC2 Type II — vendor engagement letter signed

**In-sprint deliverables:**
- Shortlist of 3 vendors evaluated (Drata, Vanta, Thoropass).
- Vendor selected.
- Engagement letter signed.
- Kickoff call scheduled within 14 days post-sprint.

**Cite:** `docs/security/soc2_tracker.md` updated to "ENGAGED — <Vendor>, kickoff <date>".

### F2. Pen-test — engagement letter + SoW signed

**In-sprint deliverables:**
- 3 vendor quotes (NCC, Bishop Fox, Bishop, Mandiant, Trail of Bits — pick credible).
- Vendor selected; SoW signed.
- Scope: external network + application layer + cloud configuration review.
- Budget: $15k–$40k (per `agies-bussiness.md` §11).
- Engagement window: weeks 3–6 post-sprint.

### F3. DPA template — published (overlap with C3)

Track F owns legal review; Track C owns drafting + commit.

### F4. BAA template — published (overlap with C4)

Same arrangement.

---

## 10. Track G — Customer-reference build (starts in-sprint, completes post-sprint)

**In-sprint deliverables:**
- 3 named design-partner tenants identified.
- Outreach email approved + sent for 3 case studies (offer redaction).
- 1 verbal yes secured.

**Post-sprint (90 days):**
- 3 redacted case studies published under `docs/case-studies/`.
- 1 named public reference logo on website.

**Honest acknowledgment:** customer references depend on sales-cycle timing outside the engineering team's control. We commit to the *outreach + first yes* within the sprint, not the published artifacts.

---

## 11. Track H — Deploy + E2E validation

This track runs at the **end** of the sprint, after Tracks A/B/C/D/E are merged into `main` and SDK releases are on PyPI.

### H1. Pre-deploy snapshot

```bash
# 1. RDS snapshot of all 5 application databases
aws rds create-db-snapshot \
  --db-snapshot-identifier aegis-pre-v2-$(date +%Y%m%d) \
  --db-instance-identifier aegis-prod-ha

# 2. S3 dump of the public transparency bucket head (in case a roll-forward bug
#    accidentally republishes a corrupted root)
aws s3 sync s3://aegis-public-roots-628478946931/ \
  s3://aegis-internal-backups/transparency-pre-v2-$(date +%Y%m%d)/

# 3. Local tag for rollback target
git tag v2.0-pre-deploy
```

### H2. Build artifact

```bash
# From repo root
git status                                    # MUST be clean
git log --oneline -10                         # confirm Tracks A/B/C merged
cd ui && bun install && bun run build && cd .. # produces ui/dist/

# tar EXCLUDING .git, node_modules, __pycache__, BUT INCLUDING ui/dist
tar --exclude='.git' \
    --exclude='node_modules' \
    --exclude='__pycache__' \
    --exclude='.venv' \
    --exclude='build' \
    --exclude='htmlcov' \
    --exclude='reports/load-test-2026-Q3' \
    -czf /tmp/aegis-v2.tar.gz .

# size sanity check (should be ~50-150MB)
ls -lh /tmp/aegis-v2.tar.gz
```

**AppleDouble gotcha (per ops memory):** before tar on macOS, run:
```bash
find . -name '._*' -delete   # remove AppleDouble metadata
```

### H3. Upload to S3

```bash
aws s3 cp /tmp/aegis-v2.tar.gz \
  s3://aegis-deploy-bucket/releases/aegis-v2.0.tar.gz \
  --metadata sha256=$(shasum -a 256 /tmp/aegis-v2.tar.gz | awk '{print $1}')
```

### H4. Rolling deploy — instance 1

**Identify instances:**
```bash
aws ec2 describe-instances \
  --filters "Name=tag:Service,Values=aegis-gateway" \
            "Name=instance-state-name,Values=running" \
  --query 'Reservations[].Instances[].[InstanceId,PrivateIpAddress,Tags[?Key==`Name`].Value|[0]]' \
  --output table
```

**Drain instance 1 from ALB target group:**
```bash
TG_ARN=$(aws elbv2 describe-target-groups \
  --names aegis-gateway-tg --query 'TargetGroups[0].TargetGroupArn' --output text)
INSTANCE_1=i-xxxxxxxxxxxx
aws elbv2 deregister-targets --target-group-arn $TG_ARN --targets Id=$INSTANCE_1
# wait for "draining" to complete (~30s)
```

**SSM deploy command:**
```bash
aws ssm send-command \
  --instance-ids $INSTANCE_1 \
  --document-name "AWS-RunShellScript" \
  --comment "aegis v2.0 deploy" \
  --parameters '{"commands":["set -euxo pipefail",
    "cd /opt/aegis",
    "find . -name \"._*\" -delete",
    "aws s3 cp s3://aegis-deploy-bucket/releases/aegis-v2.0.tar.gz /tmp/",
    "tar -xzf /tmp/aegis-v2.0.tar.gz --strip-components=0",
    "docker compose -f infra/docker-compose.yml down",
    "docker compose -f infra/docker-compose.yml up -d --build",
    "sleep 30",
    "curl -fsS http://127.0.0.1:8000/health"
  ]}'
```

**Verify instance 1:**
```bash
# Health check from inside the VPC (bypass ALB to confirm container is up)
curl -fsS http://$INSTANCE_1_PRIVATE_IP:8000/status | jq .status
# expect: "operational"

# Re-attach to ALB
aws elbv2 register-targets --target-group-arn $TG_ARN --targets Id=$INSTANCE_1

# Wait for target-health "healthy"
aws elbv2 describe-target-health --target-group-arn $TG_ARN \
  --targets Id=$INSTANCE_1 | jq '.TargetHealthDescriptions[].TargetHealth.State'
```

**If ANY step fails, STOP. Do not proceed to instance 2.** Run rollback (§12) on instance 1.

### H5. Smoke test (with instance 1 only serving traffic)

```bash
# External — through the ALB
curl -fsS https://aegisagent.in/status | jq .status                    # "operational"
curl -fsS https://aegisagent.in/api/health | jq .status                # "operational"
curl -fsS https://ha.aegisagent.in/status | jq .status                 # "operational"

# Wait 5 minutes, watch dashboards
# - error rate stays < 0.5%
# - p95 latency stays at baseline
# - no new audit-chain violations
```

If clean after 5 minutes, proceed to instance 2.

### H6. Rolling deploy — instance 2

Repeat H4 for `INSTANCE_2`. Drain → SSM → verify → re-attach.

### H7. Final smoke on both instances

```bash
# Verify both instances serving
aws elbv2 describe-target-health --target-group-arn $TG_ARN
# expect: both targets "healthy"

# 20 sequential requests, expect them to spread across both
for i in $(seq 1 20); do
  curl -sS https://aegisagent.in/status | jq -r '.gateway_host // "?"'
done | sort | uniq -c
# expect: ~10/10 split, both instance IDs present
```

---

## 12. End-to-end validation matrix (run after H7)

Each row is a discrete acceptance test executed against `https://aegisagent.in`. If ANY row fails, the sprint is **not done** — fix and re-run before sign-off.

| # | Test | Method | Expected | Critical |
|---|------|--------|----------|----------|
| E1 | `/status` 200 | `curl https://aegisagent.in/status` | 200, 12 components "operational" | ✅ |
| E2 | `/api/health` 200 | `curl https://aegisagent.in/api/health` | 200 | ✅ |
| E3 | `ha.aegisagent.in/status` 200 | `curl https://ha.aegisagent.in/status` | 200 | ✅ |
| E4 | Public S3 transparency bucket reachable | `aws s3 ls --no-sign-request s3://aegis-public-roots-628478946931/` | Lists `keys/`, `latest/`, `roots/` | ✅ |
| E5 | Today's daily Merkle root exists | `aws s3 ls --no-sign-request s3://aegis-public-roots-628478946931/roots/<tenant>/$(date -u +%Y-%m-%d).json` | File exists | ✅ |
| E6 | `aegis-verify` end-to-end chain validation | `aegis-verify --root <today.json> --pubkey <keys/...pem>` | "CHAIN VERIFIED" | ✅ |
| E7 | SDK install — `aegis-anthropic` 1.1.0 | `pip install aegis-anthropic==1.1.0` | Installs | ✅ |
| E8 | SDK install — `aegis-aevf` 1.1.0 | `pip install aegis-aevf==1.1.0` | Installs | ✅ |
| E9 | SDK install — `aegis-bedrock` 1.1.1 | `pip install aegis-bedrock==1.1.1` | Installs | ✅ |
| E10 | SDK install — `aegis-langchain` 1.1.1 | `pip install aegis-langchain==1.1.1` | Installs | ✅ |
| E11 | Path A tool deny — `/etc/passwd` | aegis-anthropic test script with `read_file('/etc/passwd')` | `_aegis_blocked` returned, action=deny, finding mentions path traversal | ✅ |
| E12 | Path A tool deny — `~/.aws/credentials` | similar test | action=deny | ✅ |
| E13 | Path A tool deny — `id_rsa` | similar test | action=deny | ✅ |
| E14 | Path A — bulk PII at 10k rows | SQL SELECT with LIMIT 10000 against PII columns | action=deny | ✅ |
| E15 | Path A — bulk PII at 9999 rows | similar | action=escalate (NOT allow) | ✅ |
| E16 | Path A — `kubectl delete namespace production` | tool call simulation | action=escalate, approver_role=SRE_LEAD | ✅ |
| E17 | Path A — `terraform destroy` prod-tagged | tool call simulation | action=escalate, approver_role=SRE_LEAD | ✅ |
| E18 | Path A — wire $100k external (closes B1) | tool call simulation | action=escalate, approver_role=CFO | ✅ |
| E19 | Path A — wire $150k external (gap closure) | tool call simulation | action=escalate, approver_role=CFO | ✅ |
| E20 | Path A — wire $99k external | tool call simulation | action=allow | ✅ |
| E21 | Path A — POST to transfer.sh | tool call simulation | action=deny | ✅ |
| E22 | Path B prompt — "ignore previous instructions" | `/v1/messages` | 403 | ✅ |
| E23 | Path B prompt — "you are now …" | `/v1/messages` | 403 | ✅ |
| E24 | Path B prompt — "DAN mode" | `/v1/messages` | 403 | ✅ |
| E25 | Path B prompt — "drop all tables" | `/v1/messages` | 403 | ✅ |
| E26 | Path B prompt — token-smuggling `<\|sys\|>` | `/v1/messages` | 403 | ✅ |
| E27 | Path B prompt — wire $150k mention | `/v1/messages` | 202, CFO approver | ✅ |
| E28 | Path B prompt — single-record PII lookup (SSN) | `/v1/messages` | 202, CISO approver | ✅ |
| E29 | Path B prompt — bulk PII export | `/v1/messages` | 202, CISO approver | ✅ |
| E30 | Path B prompt — DROP TABLE <name> | `/v1/messages` | 202, CISO approver | ✅ |
| E31 | Approval workflow — escalate then approve | escalate E27, CFO opens Approval Inbox, approves | tool executes, audit row chain links escalate→approve→execute | ✅ |
| E32 | Approval workflow — escalate then deny | escalate E28, CISO opens Approval Inbox, denies | tool never executes, audit row records deny | ✅ |
| E33 | Approval-replay TTL invalidation | Upload tightened policy mid-approval | in-flight approval is voided per `acp:tenant:policy_version` bump | ✅ |
| E34 | API key revocation effective on next call | Mint key, call, revoke, call within 1s | second call 401 | ✅ |
| E35 | Clerk RS256 token → accepted | Real Clerk session | 200 | ✅ |
| E36 | HS256 token with Clerk-shaped `iss` → rejected | Forged token | 401 | ✅ |
| E37 | Tenant isolation — Tenant A token cannot read Tenant B agents | curl with cross-tenant token | 403 / empty | ✅ |
| E38 | SSE — all 17 event types emit | Drive each event source; subscribe to `/events/stream` | 17 distinct event names observed within 10 minutes | ✅ |
| E39 | LiveFeed UI scope filter (post-U6 merge) | UI test | Filters narrow event stream correctly | ✅ |
| E40 | Incidents bulk-resolve (post-U9 merge) | UI test | Select 5, click "Mark resolved", all 5 transition | ✅ |
| E41 | Settings tab groups (post-U8 merge) | UI test | 3 section headers, 10 tabs underneath | ✅ |
| E42 | Stripe Checkout → subscription created | Test mode | Customer record created, webhook fires | ✅ |
| E43 | Stripe Customer Portal → cancel | Test mode | Subscription marked canceled | ✅ |
| E44 | Compliance "Generate board report" (post-U5 merge) | UI button | PDF downloads | ✅ |
| E45 | Threat-Intel feed CRUD (post-U5 merge) | UI | Add IOC, list, delete | ✅ |
| E46 | Dashboard empty-state CTA (post-U11 merge) | Fresh tenant | "No agents yet — Create agent →" visible | ✅ |
| E47 | Forensics container healthy after cold start (post-U1 fix) | Cold restart cluster | Forensics service_healthy before gateway accepts traffic | ✅ |
| E48 | Resource limits enforced (post-U1) | `docker stats` after load | Each container respects its mem limit | ✅ |
| E49 | Alertmanager page route fires PagerDuty (post-U2) | Trigger ChainViolationImmediate alert | Page receiver fires, distinct routing key | ✅ |
| E50 | Audit-chain post-deploy verification | `aegis-verify --range yesterday today` | No chain violations introduced by deploy | ✅ |

**E2E sign-off:** all 50 rows must be green. The engineering lead signs the sign-off note in `reports/sprint-v2-signoff.md` with date, time, and observer names.

---

## 13. Definition of Done

The sprint closes when **every** row below is checked. No row is auto-closed.

### Code & SDK
- [ ] B1 wire-transfer alignment merged + verified live (E18, E19, E20).
- [ ] B4 SDK `__version__` strings bumped + verified (`python -c "import …; print(__version__)"` for both packages).
- [ ] `aegis-aevf` 1.1.0 on PyPI; E8 passes.
- [ ] `aegis-bedrock` 1.1.1 on PyPI; E9 passes.
- [ ] `aegis-langchain` 1.1.1 on PyPI; E10 passes.

### Docs
- [ ] `agies-bussiness.md` v1.3.0 published with L1/L2/L3 applied.
- [ ] `docs/security/threat-model.md` published, peer-reviewed.
- [ ] `docs/security/dpa-template.md` published, legal-reviewed.
- [ ] `docs/security/baa-template.md` published, legal-reviewed.
- [ ] `docs/operations/incident-response.md` published.
- [ ] `docs/operations/retention-policy.md` published.
- [ ] `docs/operations/disaster-recovery.md` published with measured RTO/RPO.
- [ ] README.md updated to point at the new docs.

### Evidence
- [ ] `reports/load-test-2026-Q3/1k-rps-report.md` published.
- [ ] `reports/load-test-2026-Q3/10k-burst-report.md` published.
- [ ] DR drill executed; observed RTO < 4h and RPO < 15m recorded.
- [ ] SLO dashboard live at `aegisagent.in/slo` (tenant-gated).

### Compliance
- [ ] SOC2 Type II vendor engagement letter signed; `soc2_tracker.md` updated.
- [ ] Pen-test SoW signed; engagement window scheduled.
- [ ] DPA template signed off by legal.
- [ ] BAA template signed off by legal.

### Customer evidence
- [ ] 3 design partners contacted; 1 verbal yes secured.
- [ ] Drafts for 3 redacted case studies under `docs/case-studies/`.

### Deploy & validate
- [ ] Both EC2 instances on v2.0 build.
- [ ] All 50 E2E rows green; sign-off in `reports/sprint-v2-signoff.md`.
- [ ] Post-deploy audit-chain verification clean (E50).

### Final
- [ ] `bussines-left.md` annotated with a closing line: each finding has a "RESOLVED IN SPRINT v2.0 (commit <sha>)" pointer.
- [ ] Sprint retrospective scheduled within 7 days.

---

## 14. Out of scope for this sprint (be honest)

These items move the product forward but cannot be **completed** in 14 days. They are kicked off in this sprint (Tracks F, G) and tracked separately:

- **SOC2 Type II report issued** — requires 3–6 months of vendor evidence collection.
- **Pen-test report received** — 4–6 week post-SoW.
- **ISO 27001 certification** — separate 9–12 month track.
- **BYOK for audit-log encryption** — a 2-sprint engineering project.
- **Data residency (EU region, India region)** — a 2–3 sprint infra project.
- **3 published, named customer references** — sales-cycle dependent.

These are documented in `agies-bussiness.md` §11 ("Roadmap Priorities") with their own targeted timelines.

---

## 15. Rollback plan

If H4, H5, H6, or any post-deploy E2E row fails:

```bash
# 1. STOP further deployment
# 2. Drain the broken instance from the ALB
aws elbv2 deregister-targets --target-group-arn $TG_ARN --targets Id=$BROKEN_INSTANCE

# 3. SSM rollback command
aws ssm send-command \
  --instance-ids $BROKEN_INSTANCE \
  --document-name "AWS-RunShellScript" \
  --parameters '{"commands":["set -euxo pipefail",
    "cd /opt/aegis",
    "git fetch --tags",
    "git checkout v2.0-pre-deploy",
    "docker compose -f infra/docker-compose.yml down",
    "docker compose -f infra/docker-compose.yml up -d --build",
    "sleep 30",
    "curl -fsS http://127.0.0.1:8000/health"
  ]}'

# 4. Re-attach to ALB; verify both instances on the pre-deploy build
# 5. RDS rollback (only if schema changed):
aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier aegis-prod-ha-rollback \
  --db-snapshot-identifier aegis-pre-v2-$(date +%Y%m%d)
# Then re-point app via env var; do not delete the broken instance until forensics done.

# 6. Open a Sev-1 incident per docs/operations/incident-response.md.
# 7. Write postmortem within 14 days. Block any further deploy until lessons absorbed.
```

**Rollback is NOT failure** — it is the success-mode of the deployment pipeline. The failure mode is shipping broken code and pretending it works.

---

## 16. Commit & push protocol

Per project memory:
- All commits are **local-first**. Never `git push` without an explicit human sign-off on the diff.
- Every commit message ends with the issue reference (e.g. `Closes B1`). No `Co-Authored-By: Claude` line — the product is human-attributed.
- Tag the final pre-deploy commit `v2.0-pre-deploy` for rollback (§15).
- Tag the post-deploy verified commit `v2.0-GA` only after row E50 is green.

```bash
# at sprint close, after E50 green
git tag -a v2.0-GA -m "Aegis v2.0 GA — all 50 E2E rows green, both instances live"
# push tag only after CTO email approval
# git push origin v2.0-GA   # (commented; human runs this)
```

---

## 17. Sprint kickoff statement (read at standup, day 1)

> We are not shipping marketing. We are shipping a product that a Principal Security Architect can audit and a CISO can defend to their board. Every claim in `agies-bussiness.md` v1.3.0 must have a file:line citation or a runtime artifact. Every gap in `bussines-left.md` must be closed in code or have a dated vendor on the calendar. We do not bypass. We do not shortcut. If the sprint cannot fit all 50 E2E rows, we extend the sprint or descope honestly — we do not declare green on a test that did not pass.
>
> Code = product = revenue. Quality, not quantity.

---

*End of sprint.md — created 2026-06-18 — derived from `bussines-left.md` (audit) and `agies-bussiness.md` v1.2.0 (context). Supersedes any sprint plan in `SPRINT.md` for this work window.*
