# ServiceNow ITSM — setup runbook

Walkthrough for a ServiceNow admin to connect their instance to Aegis so
that every new Aegis incident automatically opens a ServiceNow Incident
(table: `incident`), and so retries de-dupe on the same record.

Sprint EI-6 (2026-06-20). Sister of `docs/security/okta-scim-setup.md`
(Okta SCIM) and the Jira integration setup.

---

## ServiceNow side — create a dedicated service account

A dedicated, MFA-exempt account whose only purpose is creating + updating
Aegis incidents. Do NOT use a human admin's credentials — they rotate,
they have MFA, they leave the company.

1. In the SNOW admin console, **System Security → Users → New**.
2. Required fields:
   - **User ID**: `aegis_bot` (or similar — must be unique)
   - **First name** / **Last name**: anything (used in audit trail only)
   - **Email**: `aegis-snow@yourcompany.com` (real or alias OK)
   - **Web service access only**: ☑ **true**
     (This account cannot sign into the SNOW UI — bearer-style use only.)
3. Click **Submit**, then re-open the row.
4. **Set Password** → generate a 32+ char password, copy it now (you'll
   paste it into Aegis Settings next). MFA must remain disabled for this
   account; the SNOW Table API does not support MFA.
5. **Roles** → add `itil` (lets the user create + update Incidents)
   and `web_service_admin` (some SNOW versions require this for REST).
   If your SNOW instance has a more-scoped role like `aegis_incident_creator`,
   prefer that — `itil` is the minimum that works out of the box.

### Optional: scope by ACL

For a tighter posture, create a custom role that grants exactly:

```text
create  on table=incident
read    on table=incident         (so SNOW can return the new sys_id)
write   on table=incident         (so future updates can land)
```

…and revoke `itil`. The Aegis integration writes only to the `incident`
table; no read access elsewhere is needed.

---

## Aegis side — paste the credentials

1. Sign into Aegis as **OWNER** or **ADMIN**.
2. Navigate to **Settings → Integrations → ServiceNow**.
3. Fill in:
   - **Instance URL**: `https://your-org.service-now.com`
     (no trailing slash; HTTPS required — Aegis rejects `http://`
     except for loopback test mode)
   - **Service account username**: `aegis_bot`
   - **Password**: paste the 32-char password from SNOW
   - **Default urgency** / **Default impact**: 1=High, 2=Medium, 3=Low
     (only used for tickets opened via the Test button — auto-opened
     tickets read severity from the Aegis incident itself)
   - **Default category** (optional): e.g. `software` or your custom
     security category sys_id
   - **Default assignment group sys_id** (optional): paste the 32-char
     sys_id of the SNOW group that should own Aegis tickets — found
     under **User Administration → Groups** → click your group →
     copy the sys_id from the URL bar
4. Leave both **Integration enabled** and **Auto-create on incident**
   checked.
5. Click **Save**, then **Test connection**.
6. If "Test incident created: INC0010001" appears, open SNOW and verify
   the test ticket is in the chosen project with the right assignment
   group. Close the test ticket immediately — it's labelled "safe to
   close".

---

## What Aegis actually sends

For every new Aegis incident, the integration POSTs to:

```text
POST https://<instance>/api/now/table/incident
Authorization: Basic base64(<username>:<password>)
Content-Type: application/json

{
  "short_description": "[Aegis CRITICAL] sql_injection",
  "description":       "Aegis opened incident I-abc-123 …",
  "urgency":           "1",
  "impact":            "1",
  "category":          "security",
  "assignment_group":  "<sys_id>",
  "correlation_id":    "I-abc-123"
}
```

The `correlation_id` is the Aegis `incident_id`. SNOW de-dupes on this
value — a retry of the same incident does NOT open a second ticket; SNOW
returns the existing record's `sys_id` instead. This is how the
incident_watcher's auto-retry loop stays idempotent.

Severity → urgency/impact mapping (in
`services/autonomy/incident_watcher.py:_severity_to_snow_levels`):

| Aegis severity | SNOW urgency | SNOW impact | SNOW computed priority |
|---|---|---|---|
| CRITICAL | 1 (High)   | 1 (High)   | 1 - Critical |
| HIGH     | 1 (High)   | 2 (Medium) | 2 - High |
| MEDIUM   | 2 (Medium) | 2 (Medium) | 3 - Moderate |
| LOW      | 3 (Low)    | 3 (Low)    | 5 - Planning |
| anything else | 2 | 2 | 3 - Moderate |

SNOW computes `priority` from urgency × impact; Aegis does not send it
explicitly.

---

## What Aegis does NOT do

- **No SNOW table other than `incident`** is touched. Aegis will not
  open Change Requests, Problems, or Service Catalog Requests.
- **No outbound webhook from SNOW back to Aegis** is wired today.
  Resolving the SNOW ticket does NOT close the Aegis incident — they
  are linked only by sys_id reference for human triage convenience.
  A future sprint may add a SNOW Business Rule → Aegis webhook to
  close the loop.
- **No SNOW user provisioning**. Use the Okta SCIM integration
  (`docs/security/okta-scim-setup.md`) if you want Aegis users
  auto-provisioned; SNOW user provisioning is a separate Okta connector.

---

## Revocation + rotation

- **Revoke**: Settings → Integrations → ServiceNow → Remove. Next
  Aegis incident logs `snow_create_skipped reason=missing_config`.
- **Rotate password**: change it on SNOW, then re-Save in Aegis with
  the new value. The old password becomes unusable immediately; in-
  flight retries silently switch to the new value on next call.
- **Rotation cadence**: align with your SNOW org's policy (typically
  90 days for service accounts). Aegis does not auto-rotate.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Test returns `401 User Not Authenticated` | Password wrong, or account is locked, or `itil` role missing | SNOW → re-set password, verify role grants, retry |
| Test returns `403` | Account exists + auth OK but lacks `create` on `incident` table | Add `itil` role OR custom create-only role |
| Test returns `created` but ticket is in wrong group | Default assignment group sys_id wrong | Re-verify the sys_id from the SNOW Groups page; sys_id is 32 hex chars |
| Auto-created tickets stop appearing after working before | Service account password rotated outside Aegis | Re-Save in Aegis with the new password |
| Two SNOW tickets opened for one Aegis incident | Aegis incident_id is missing → no correlation_id → SNOW can't dedupe | Check the originating incident has a populated `id` field; this is a bug, file an issue |
