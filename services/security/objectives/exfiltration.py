"""
MITRE ATT&CK TA0010 — Exfiltration.

Where the chain completes. Five signal classes:

  * `known_exfil_destination` — request target is on the curated bad-host
    list (transfer.sh / pastebin / webhook.site / etc.). Tier=deny on its
    own — pulling agent traffic to those hosts is never legitimate.
  * `external_pii_exfil` — the combination signal: external POST + PII in
    body + (host on bad-list OR host is personal email gateway), OR a
    shell command that combines an exfil host with a PII-shaped filename.
    This is the "tar+curl→transfer.sh" pattern resolved in a single call.
  * `external_post_pii_unknown_dest` — external POST with PII body to a
    host NOT on the bad-list (Sprint 1 GAP-3). Escalate-tier; brand-new
    attacker hosts shouldn't slip past just because they're not curated.

Inputs from canonical:
  action_type, url_host, is_known_exfil_dest, is_external_url, raw_norm,
  command_norm.

The `_KNOWN_EXFIL_DESTS` constant lives in canonical.py for now. Sprint 7
will replace it with a pluggable ThreatIntelProvider.
"""
from __future__ import annotations

import re


# Sprint U13 — high-confidence secret patterns. Each one is anchored on a
# vendor prefix + minimum body length so we don't false-positive on
# "Bearer foo" or short hex strings. Order doesn't matter (we union).
_SECRET_PATTERNS = (
    re.compile(r"\bsk-ant-(?:api|key)?[0-9a-z_-]{20,}", re.I),   # Anthropic
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{40,}"),            # OpenAI
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                          # AWS access key id
    re.compile(r"aws_secret_access_key[\"'\s:=]+[A-Za-z0-9/+]{30,}", re.I),
    re.compile(r"\bxox[abprs]-[0-9A-Za-z-]{10,}"),               # Slack
    re.compile(r"\bghp_[A-Za-z0-9]{30,}"),                        # GitHub PAT
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),  # JWT
)


_PII_MARKERS = (
    "customer", "customers", "patient", "patients",
    "ssn", "credit_card", "creditcard", "passport",
    "users.csv", "user_export", "payroll",
    "tax_id", "social_security",
)
_PERSONAL_MAIL_HOSTS = (
    "gmail.com", "yahoo.com", "hotmail.com",
    "outlook.com", "proton.me", "icloud.com",
)
_SENDMAIL_TOOLS = ("sendmail", "mailx ", "mail -s", "swaks", "msmtp")
_BENIGN_EXTERNAL_HOSTS = ("api.weatherapi.com",)  # seed allow-list


def detect(c: dict, known_exfil_dests: tuple[str, ...] = ()) -> list[str]:
    findings: list[str] = []
    raw = (c.get("raw_norm") or "").lower()
    cmd_norm = (c.get("command_norm") or "").lower()
    host = (c.get("url_host") or "").lower()

    if c.get("is_known_exfil_dest"):
        findings.append("known_exfil_destination")

    # external POST + known exfil dest + PII body → pure exfil
    if (c.get("action_type") == "external_post"
            and c.get("is_known_exfil_dest")
            and any(t in raw for t in _PII_MARKERS)):
        if "external_pii_exfil" not in findings:
            findings.append("external_pii_exfil")

    # shell that combines an exfil host AND a PII marker in the same command
    if cmd_norm and known_exfil_dests:
        if any(h in cmd_norm for h in known_exfil_dests):
            if any(t in cmd_norm for t in _PII_MARKERS):
                if "external_pii_exfil" not in findings:
                    findings.append("external_pii_exfil")
        # personal-mail sendmail + PII (or to-personal-domain)
        if any(m in cmd_norm for m in _SENDMAIL_TOOLS):
            to_personal = any(t in cmd_norm for t in
                              ("@" + d for d in _PERSONAL_MAIL_HOSTS))
            pii_in_cmd = any(t in cmd_norm for t in _PII_MARKERS)
            if to_personal or pii_in_cmd:
                if "external_pii_exfil" not in findings:
                    findings.append("external_pii_exfil")

    # external POST → personal-mail host + PII raw blob = paste-style exfil
    if (c.get("action_type") == "external_post"
            and host in _PERSONAL_MAIL_HOSTS
            and any(t in raw for t in ("ssn", "credit_card", "patient", "customer"))):
        if "external_pii_exfil" not in findings:
            findings.append("external_pii_exfil")

    # external POST to UNKNOWN host with PII body → escalate (GAP-3)
    if (c.get("action_type") == "external_post"
            and c.get("is_external_url")
            and not c.get("is_known_exfil_dest")
            and host not in _BENIGN_EXTERNAL_HOSTS
            and "external_pii_exfil" not in findings):
        if any(t in raw for t in _PII_MARKERS):
            findings.append("external_post_pii_unknown_dest")

    # Sprint U13 2026-06-26 — credential-shaped pattern in any outbound
    # message body. Covers send_email, http_request, slack post_message,
    # webhook calls, etc. Catches §32-C-10 (Bearer sk-ant-... in an email
    # body). raw_norm gets lowercased upstream so the case-insensitive
    # patterns above stay correct; the AWS-key and JWT regexes that need
    # original case are matched against the raw string before lowercase.
    raw_orig = (c.get("raw_norm_original") or c.get("raw_norm") or "")
    if any(p.search(raw_orig) for p in _SECRET_PATTERNS):
        if "credential_in_message_body" not in findings:
            findings.append("credential_in_message_body")

    return findings
