# Jira ITSM — setup runbook

Walkthrough for a Jira admin to connect Atlassian Jira Cloud to Aegis so
every new Aegis incident automatically opens a Jira issue, and resolving
that issue automatically closes the Aegis incident.

Sprint EI-2 (outbound, ticket creation) + Sprint EI-17 (inbound,
round-trip close) + Sprint EI-18 (Settings-UI rotate + this runbook).
Sister of `docs/security/servicenow-itsm-setup.md`.

---

## 1. Jira side — create an API token

The outbound integration (Aegis opens Jira issues) needs an Atlassian
API token from a dedicated service account. Don't use a human admin's
token — they rotate, they have MFA, they leave the company.

1. Sign into Atlassian at <https://id.atlassian.com>.
2. Pick (or create) a service-account user — e.g. `aegis-bot@yourcompany.com`.
3. **Account settings → Security → Create and manage API tokens →
   Create API token**.
4. Label: `Aegis`. Copy the token now — Atlassian doesn't show it again.
5. Grant the service account at least **Browse projects** + **Create
   issues** + **Edit issues** + **Resolve issues** on the project you
   want Aegis tickets to land in.

---

## 2. Aegis side — paste credentials + generate webhook secret

1. Sign into Aegis as **OWNER** or **ADMIN**.
2. **Settings → Integrations → Jira**.
3. Fill in:
   - **Atlassian base URL**: `https://your-org.atlassian.net`
   - **Project key**: the Jira project where Aegis tickets should land
     (e.g. `SEC`)
   - **Service account email**: the `aegis-bot@...` from step 1.2
   - **API token**: paste the value from step 1.4
   - **Default issue type**, **Default priority**: per your project
4. Click **Save**, then **Test connection**. Verify the test issue
   "Aegis connection test — safe to close" appears in your Jira project.

5. Scroll to the **Inbound webhook** section.
6. Click **Generate secret**.
7. A banner appears with the **Webhook URL** and the **HMAC secret**.
   Copy both — Aegis doesn't show the secret again. Dismiss the banner
   only after you have both in your clipboard.

---

## 3. Jira side — Automation rule that closes the loop

1. In Jira: **Project settings → Automation → Create rule**.
2. **Trigger**: *Issue transitioned*.
3. **Components**: keep defaults — the rule fires on any transition.
4. **Condition** (optional but recommended): *Issue fields condition* →
   `Status = Done` (or `Resolved`, or whatever your final state is).
   Without this, every transition will POST and most will be
   `ignored` server-side.
5. **Action**: *Send web request*.
   - URL: paste the webhook URL from Aegis (Step 2.7).
     Looks like `https://aegisagent.in/webhooks/jira/<TENANT_UUID>`.
   - HTTP method: `POST`
   - Web request body: **Custom data**
   - Web request body:
     ```json
     {
       "webhookEvent": "jira:issue_updated",
       "issue": {
         "key": "{{issue.key}}",
         "fields": { "status": { "name": "{{issue.status.name}}" } }
       }
     }
     ```
   - Headers: add a custom header `X-Hub-Signature-256` whose value is
     the HMAC-SHA256 of the body using the secret from Step 2.7.
     - In Jira Automation, this is a smart value:
       `{{webhookBody.toHmac256("YOUR_SECRET_HERE")}}` (replace
       `YOUR_SECRET_HERE` with the secret you copied).
6. **Publish** the rule.

---

## 4. Verify end-to-end

1. Cause an Aegis incident (any policy-deny on a demo tenant works).
2. Within ~5 seconds, a Jira issue appears in the configured project
   tagged `aegis` + `sev-<severity>`. The issue summary starts with
   `[Aegis <severity>] ...`.
3. In Aegis: **Incidents** page shows the new incident with a
   `jira_issue_key` link in its timeline.
4. In Jira: **transition the issue to Done**.
5. Within ~5 seconds, the Aegis incident moves to `RESOLVED`. The
   incident timeline shows `event: external_link, by:
   jira-webhook:SEC-NN`.

---

## 5. Rotate the secret

When a Jira admin who knew the secret leaves the org, OR every 365 days
as defence-in-depth:

1. In Aegis: **Settings → Integrations → Jira → Inbound webhook →
   Rotate secret**. The old secret stops working immediately; the new
   one shows in a one-time banner.
2. In Jira: update the Automation rule's
   `{{webhookBody.toHmac256("...")}}` parameter to the new secret.
3. Test: transition a freshly-created Aegis-linked issue to Done; the
   Aegis incident should close.

There's no Aegis-imposed expiry on the secret — rotate when the
operational situation demands it.

---

## 6. Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| Test issue creation returns 401 | API token wrong, or service account lacks Create | Re-create the token in Atlassian; re-grant project perms |
| Jira issue created but Aegis incident has no `jira_issue_key` | `incident_link_back_failed` in `acp_autonomy` logs — api-svc was unreachable when the link-back fired | Restart `acp_autonomy` + `acp_api`; future tickets link cleanly |
| Jira issue transitioned to Done but Aegis incident stays OPEN | Webhook rule not firing, OR HMAC signature wrong | Open the Automation audit log in Jira: should show `Web request: 200`. If 401, the secret/HMAC mismatches — rotate in Aegis + re-paste into the rule. If 200 but Aegis didn't close, check `acp_gateway` logs for `jira_webhook_unknown_issue_key` (means Aegis never linked-back; cure as above row) |
| Webhook returns 200 + `status: ignored` | Transition wasn't to a done-like status (Done / Resolved / Closed / Complete / Completed) | Either pick a different trigger condition, OR expand the JIRA_DONE_NAMES set in `services/gateway/routers/itsm_webhooks.py` |
| Many auto-created Jira issues for the same Aegis incident | The Jira service-account lacks "Resolve" — Jira creates a NEW issue every time Aegis re-fires. Aegis uses idempotency via stored issue_key, but if the link-back failed (see above), the next /handle_incident pass will create a new issue | Grant Resolve on the project; root-cause the link-back failure |

---

## 7. Attaching the AEVF bundle to a Jira ticket (Sprint EI-19)

For SOC 2 / EU AI Act evidence — every Aegis incident has a
downloadable, cryptographically-signed bundle of the underlying audit
events. The auditor verifies it offline with `aegis-verify` (no network
call to Aegis required).

1. In Aegis → **Incidents** → open the incident → click **AEVF bundle**.
2. A `aegis-incident-INC-NNN.aevf.json` file downloads.
3. In Jira → open the linked issue → drag the JSON into the
   **Attachments** panel. Add a comment: "Aegis cryptographic evidence
   — verify with `pip install aegis-aevf && aegis-verify --bundle <file>`".

The bundle contains every public key, the signed daily Merkle root for
the days the incident touched, and every audit row tied to it. Any
post-hoc tampering invalidates the signature; the auditor sees that
immediately.

---

## 8. What this integration does NOT do

- **No Jira-side custom fields populated.** Aegis writes only the four
  required fields: project, summary, description, issuetype (plus
  priority + labels if configured). If your project requires custom
  fields on create, you'll get a validation error — work around by
  setting Jira-side defaults for those fields.
- **No bidirectional comment sync.** Comments on the Jira issue don't
  sync into the Aegis incident's timeline; only the final "Done"
  transition does.
- **No Jira-issue-deleted → Aegis-incident-deleted.** Deletion is
  irreversible in both systems by policy; we don't mirror that.
