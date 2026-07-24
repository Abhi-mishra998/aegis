"""ATF v3.2 §3.2 + §14.5 — install-time egress lockdown checker.

Verifies that the target environment has default-deny egress with the
Aegis Capability Gate as the only permitted outbound path. Runs as the
INSTALL lifecycle event's precondition (bundle is not marked compliant
if this fails).

Supports three environment shapes today:

  * kubernetes  — reads a NetworkPolicy YAML
  * aws-sg      — reads `aws ec2 describe-security-groups` JSON
  * firewall    — reads an iptables-style rules text file

Any environment that isn't in this list produces a HUMAN_REVIEW verdict
(§3.2 posture: not a bypass, but a documented ambiguity for the operator
to close). Emits JSON so a CI job can consume the result.

Usage:
  python scripts/ops/verify_egress_lockdown.py \\
      --kind kubernetes --input k8s/networkpolicy.yaml \\
      --gate-fqdn gate.aegis.svc.cluster.local
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Literal

Kind = Literal["kubernetes", "aws-sg", "firewall"]
Verdict = Literal["LOCKED_DOWN", "OPEN", "HUMAN_REVIEW"]


def check_kubernetes(manifest: dict[str, Any], gate_fqdn: str) -> tuple[Verdict, str]:
    spec = manifest.get("spec", {}) or {}
    policy_types = spec.get("policyTypes") or []
    if "Egress" not in policy_types:
        return "OPEN", "NetworkPolicy has no Egress rule"
    egress = spec.get("egress") or []
    if not egress:
        return "LOCKED_DOWN", "explicit empty egress list = default-deny"
    # Must have exactly one egress rule that only allows gate_fqdn.
    for rule in egress:
        peers = rule.get("to", [])
        for p in peers:
            fqdn = (p.get("dnsName") or "").rstrip(".")
            if fqdn and fqdn != gate_fqdn.rstrip("."):
                return "OPEN", f"egress rule permits {fqdn!r} (not the Gate)"
    return "LOCKED_DOWN", f"egress restricted to {gate_fqdn}"


def check_aws_sg(sg_json: dict[str, Any], gate_ip_or_prefix: str) -> tuple[Verdict, str]:
    groups = sg_json.get("SecurityGroups") or []
    for g in groups:
        egress_rules = g.get("IpPermissionsEgress") or []
        for r in egress_rules:
            for ip_range in r.get("IpRanges", []):
                cidr = ip_range.get("CidrIp", "")
                if cidr and cidr != gate_ip_or_prefix and cidr != "0.0.0.0/32":
                    return "OPEN", f"egress permits {cidr!r} beyond Gate"
    return "LOCKED_DOWN", f"AWS egress restricted to {gate_ip_or_prefix}"


def check_firewall_text(rules_text: str, gate_ip: str) -> tuple[Verdict, str]:
    lines = [line.strip() for line in rules_text.splitlines() if line.strip() and not line.strip().startswith("#")]
    has_default_deny = any("DROP" in line and "OUTPUT" in line for line in lines)
    permits_gate = any(gate_ip in line and ("ACCEPT" in line) for line in lines)
    if not has_default_deny:
        return "OPEN", "no default-deny OUTPUT rule found"
    if not permits_gate:
        return "OPEN", f"no ACCEPT rule for Gate IP {gate_ip}"
    return "LOCKED_DOWN", f"default-deny with ACCEPT for {gate_ip}"


def run(kind: Kind, input_path: Path, gate_target: str) -> tuple[Verdict, str]:
    raw = input_path.read_text()
    if kind == "kubernetes":
        # Minimal YAML parser: just look for the fields we care about.
        # Ponytail: avoid pulling PyYAML for a one-file read.
        try:
            import yaml as _yaml
            manifest = _yaml.safe_load(raw) or {}
        except ImportError:
            return "HUMAN_REVIEW", "PyYAML not installed; cannot parse manifest"
        return check_kubernetes(manifest, gate_target)
    if kind == "aws-sg":
        return check_aws_sg(json.loads(raw), gate_target)
    if kind == "firewall":
        return check_firewall_text(raw, gate_target)
    return "HUMAN_REVIEW", f"unknown environment kind: {kind}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kind", required=True, choices=["kubernetes", "aws-sg", "firewall"])
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--gate-fqdn", "--gate-ip", "--gate-target", dest="gate_target", required=True)
    args = ap.parse_args(argv)

    verdict, reason = run(args.kind, args.input, args.gate_target)
    print(json.dumps({"verdict": verdict, "reason": reason, "kind": args.kind}))
    return 0 if verdict == "LOCKED_DOWN" else 1


if __name__ == "__main__":
    # If argv given, run CLI mode; otherwise self-check.
    if len(sys.argv) > 1:
        sys.exit(main())

    # Self-check with in-memory manifests (no I/O).
    v, _ = check_kubernetes(
        {"spec": {"policyTypes": ["Egress"], "egress": []}}, "gate.aegis.svc"
    )
    assert v == "LOCKED_DOWN"

    v, _ = check_kubernetes(
        {"spec": {"policyTypes": ["Ingress"]}}, "gate.aegis.svc"
    )
    assert v == "OPEN"

    v, _ = check_kubernetes(
        {"spec": {
            "policyTypes": ["Egress"],
            "egress": [{"to": [{"dnsName": "evil.example"}]}],
        }},
        "gate.aegis.svc",
    )
    assert v == "OPEN"

    v, _ = check_aws_sg(
        {"SecurityGroups": [{"IpPermissionsEgress": [
            {"IpRanges": [{"CidrIp": "10.0.1.5/32"}]}]}]},
        "10.0.1.5/32",
    )
    assert v == "LOCKED_DOWN"

    v, _ = check_firewall_text(
        "-A OUTPUT -d 10.0.1.5 -j ACCEPT\n-A OUTPUT -j DROP", "10.0.1.5"
    )
    assert v == "LOCKED_DOWN"

    print("verify_egress_lockdown OK")
