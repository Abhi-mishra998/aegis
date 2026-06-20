#!/usr/bin/env python3
"""Sprint EI-13 — diff today's SBOM CVE list against yesterday's.

The nightly_verify workflow calls this AFTER running sbom_cve_scan.sh
on today's CycloneDX SBOM. Yesterday's snapshot is fetched from
``s3://aegis-public-roots-628478946931/cve-history/yesterday.json``
(the workflow keeps a rolling 30-day archive at
``cve-history/<YYYY-MM-DD>.json`` for forensic spot-checks).

Why a diff instead of "just fail on any HIGH/CRITICAL"?

The Aegis dependency tree has chronic-unfixed HIGH/CRITICAL CVEs —
upstreams that won't ship a fix, indirect deps we cannot upgrade
without breaking the SDK, etc. Failing on every HIGH/CRITICAL means
the nightly job is RED every night and the operator stops looking at
it. A diff means "RED only when something CHANGED" — that's the
signal worth waking up to.

This script writes three artefacts:
  - the diff itself as ``--out`` (3 buckets: new / resolved / chronic)
  - a short markdown summary for the GH-Actions step summary
  - a single-line status word (``pass`` / ``new-cves``) to stdout that
    the workflow inspects directly

Exit codes:
  0  no NEW CVEs at or above SEVERITY floor
  1  one or more NEW CVEs detected
  2  bad arguments / missing inputs
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

SEVERITY_ORDER = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


@dataclass(frozen=True)
class CveKey:
    """One CVE × one package × one installed version. Identity for diffing."""
    id: str
    package: str
    installed_version: str

    @classmethod
    def from_finding(cls, f: dict) -> "CveKey":
        return cls(
            id=str(f.get("id") or ""),
            package=str(f.get("package") or ""),
            installed_version=str(f.get("installed_version") or ""),
        )


def _load(p: Path | None) -> list[dict]:
    """Load a sbom_cve_scan.sh-style JSON array. Missing file = empty list."""
    if p is None or not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except Exception:
        return []
    return data if isinstance(data, list) else []


def _meets_floor(finding: dict, floor: str) -> bool:
    sev = (finding.get("severity") or "").upper()
    return SEVERITY_ORDER.get(sev, 0) >= SEVERITY_ORDER.get(floor.upper(), 0)


def diff(
    today: list[dict],
    yesterday: list[dict],
    *,
    severity_floor: str = "HIGH",
) -> dict:
    """Return {new, resolved, chronic, summary} buckets.

    - new      = in today, NOT in yesterday, at-or-above floor
    - resolved = in yesterday, NOT in today (any severity — resolved-good
                 news goes in the report regardless of floor)
    - chronic  = in both (same CveKey) — informational only

    A CVE that drops below the severity floor (e.g. re-classified from
    HIGH to MEDIUM upstream) appears as `resolved` so the operator sees
    the change.
    """
    today_at_floor = {CveKey.from_finding(f): f for f in today if _meets_floor(f, severity_floor)}
    yesterday_at_floor = {CveKey.from_finding(f): f for f in yesterday if _meets_floor(f, severity_floor)}

    new_keys      = sorted(today_at_floor.keys() - yesterday_at_floor.keys(),
                           key=lambda k: (k.id, k.package))
    resolved_keys = sorted(yesterday_at_floor.keys() - today_at_floor.keys(),
                           key=lambda k: (k.id, k.package))
    chronic_keys  = sorted(today_at_floor.keys() & yesterday_at_floor.keys(),
                           key=lambda k: (k.id, k.package))

    return {
        "severity_floor": severity_floor,
        "counts": {
            "new":      len(new_keys),
            "resolved": len(resolved_keys),
            "chronic":  len(chronic_keys),
        },
        "new":      [today_at_floor[k]      for k in new_keys],
        "resolved": [yesterday_at_floor[k]  for k in resolved_keys],
        "chronic":  [today_at_floor[k]      for k in chronic_keys],
    }


def render_markdown(diff_result: dict) -> str:
    """Render a short summary suitable for the GH-Actions step summary."""
    out: list[str] = []
    c = diff_result["counts"]
    floor = diff_result["severity_floor"]
    out.append(f"### Nightly SBOM CVE diff (severity ≥ {floor})")
    out.append("")
    out.append(f"| New | Resolved | Chronic |")
    out.append(f"|----:|---------:|--------:|")
    out.append(f"| **{c['new']}** | {c['resolved']} | {c['chronic']} |")
    if diff_result["new"]:
        out.append("")
        out.append("#### New CVEs (this is the signal)")
        out.append("")
        # Sprint EI-15 — `source` column tells the operator WHERE the
        # CVE came from (python-sbom vs image:<ref>). Default value
        # 'python-sbom' applies to entries that lack a source tag
        # (i.e. produced by sbom_cve_scan.sh before EI-15 was wired).
        out.append("| CVE | Severity | Source | Package | Installed | Fixed in | Link |")
        out.append("|---|---|---|---|---|---|---|")
        for f in diff_result["new"]:
            url = f.get("primary_url") or ""
            fixed = f.get("fixed_version") or "—"
            source = f.get("source") or "python-sbom"
            out.append(
                f"| {f.get('id', '?')} | {f.get('severity', '?')} | "
                f"{source} | "
                f"{f.get('package', '?')} | {f.get('installed_version', '?')} | "
                f"{fixed} | [link]({url}) |"
            )
    if diff_result["resolved"]:
        out.append("")
        out.append("#### Resolved (no longer in today's scan)")
        out.append("")
        for f in diff_result["resolved"]:
            out.append(f"- {f.get('id', '?')} ({f.get('package', '?')} {f.get('installed_version', '?')})")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sbom_cve_diff",
                                description="Diff today's SBOM CVE scan vs yesterday's.")
    p.add_argument("--today",     required=True, type=Path,
                   help="Today's sbom_cve_scan.sh JSON output.")
    p.add_argument("--yesterday", type=Path, default=None,
                   help="Yesterday's archived JSON (omit / missing = empty baseline).")
    p.add_argument("--out",       type=Path, default=Path("/tmp/cve-diff.json"),
                   help="Where to write the diff JSON.")
    p.add_argument("--summary",   type=Path, default=None,
                   help="Optional markdown summary path for GH step summary.")
    p.add_argument("--severity-floor", default="HIGH",
                   choices=["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                   help="Ignore CVEs below this severity. Default: HIGH.")
    args = p.parse_args(argv)

    if not args.today.exists():
        print(f"FAIL — today's scan file not found: {args.today}", file=sys.stderr)
        return 2

    today_findings     = _load(args.today)
    yesterday_findings = _load(args.yesterday)

    result = diff(today_findings, yesterday_findings, severity_floor=args.severity_floor)
    args.out.write_text(json.dumps(result, indent=2))

    if args.summary:
        args.summary.write_text(render_markdown(result))

    n_new = result["counts"]["new"]
    print("pass" if n_new == 0 else "new-cves")
    return 0 if n_new == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
