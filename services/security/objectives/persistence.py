"""
MITRE ATT&CK TA0003 — Persistence.

The agent surface persists when it writes credential-shaped artefacts
(SSH authorized_keys, AWS profile config, .creds drop files). The
canonical extractor flags this on shell + file_write paths; here we
just translate the flag into the registered signal name.
"""
from __future__ import annotations


_SHELL_CRED_PATTERNS = (
    "/tmp/.creds", "/tmp/.aws/credentials",
    ".bash_history", "/home/.aws", "/home/.ssh",
    "echo backdoor", "echo creds", "creds >",
    "credentials >>", "id_rsa >>", "authorized_keys",
)
_FILE_CRED_TOKENS = (".creds", "credentials", "id_rsa", "authorized_keys")


def detect(c: dict) -> list[str]:
    findings: list[str] = []
    cmd_norm = (c.get("command_norm") or "").lower()
    if cmd_norm and any(p in cmd_norm for p in _SHELL_CRED_PATTERNS):
        findings.append("credential_artifact_write")
        return findings  # one signal is enough; avoid double-emit below
    file_path = (c.get("file_path") or "").lower()
    if (c.get("action_type") == "file_write"
            and file_path
            and any(t in file_path for t in _FILE_CRED_TOKENS)):
        findings.append("credential_artifact_write")
    return findings
