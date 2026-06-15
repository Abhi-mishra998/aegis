"""
MITRE ATT&CK TA0006 — Credential Access.

Reads of credential-shaped paths from the agent surface.

  * `cloud_credential_path` — /root/.aws, /etc/kubernetes/admin.conf,
    /root/.docker, etc. Distinguished from the SSH variant because the
    SOC remediation playbook is different (rotate cloud IAM vs rotate
    SSH keys).
  * `ssh_credential_path` — id_rsa, id_ed25519, authorized_keys,
    .ssh/config.
  * `system_sensitive_path` — /etc/passwd, /etc/shadow, /proc/self,
    /etc/aegis, etc. Not strictly credentials but the same blast radius
    (credentials usually leak from here).

The canonical extractor already classifies the path into either
`cred_path` (cloud / SSH) or `sensitive_path` (system). Here we just
split `cred_path` into the two flavours by inspecting the filename.
"""
from __future__ import annotations


_CLOUD_TOKENS = ("aws", "docker", "kube", "gcloud", "azure")


def detect(c: dict) -> list[str]:
    findings: list[str] = []
    if c.get("sensitive_path"):
        findings.append("system_sensitive_path")
    if c.get("cred_path"):
        fp = (c.get("file_path") or "").lower()
        if any(t in fp for t in _CLOUD_TOKENS):
            findings.append("cloud_credential_path")
        else:
            findings.append("ssh_credential_path")
    return findings
