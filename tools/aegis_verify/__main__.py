"""CLI: `python -m aegis_verify --bundle bundle.json`"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import SPEC_VERSION, __version__
from .verifier import SUPPORTED_FORMATS, verify_bundle


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="aegis-verify",
        description=(
            "Offline verifier for Aegis evidence bundles. Validates per-row "
            "event_hash, prev_hash chain, ed25519 Merkle root signatures, and "
            "the daily-root chain — with zero network calls to Aegis. "
            f"Reference implementation of AEVF spec {SPEC_VERSION}."
        ),
    )
    p.add_argument("--bundle", type=Path,
                   help="path to the evidence-bundle JSON file")
    p.add_argument("--verbose", action="store_true",
                   help="print every check, not just failures")
    p.add_argument("--json", action="store_true",
                   help="emit a machine-readable report on stdout")
    p.add_argument("--print-spec-version", action="store_true",
                   help="print the AEVF specification version this "
                        "implementation conforms to + supported bundle "
                        "formats, then exit. Use to pin an auditor runbook "
                        "to a known spec version.")
    args = p.parse_args(argv)

    if args.print_spec_version:
        print(json.dumps({
            "spec_version":              SPEC_VERSION,
            "implementation_version":    __version__,
            "supported_bundle_formats":  sorted(SUPPORTED_FORMATS),
        }, indent=2))
        return 0

    if not args.bundle:
        p.error("--bundle is required (unless --print-spec-version)")

    try:
        bundle = json.loads(args.bundle.read_text())
    except FileNotFoundError:
        print(f"error: bundle file not found: {args.bundle}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"error: bundle is not valid JSON: {exc}", file=sys.stderr)
        return 2

    report = verify_bundle(bundle)

    if args.json:
        out = {
            "passed":            report.passed,
            "bundle_format":     report.bundle_format,
            "framework":         report.framework,
            "tenant_id":         report.tenant_id,
            "record_count":      report.record_count,
            "merkle_root_count": report.merkle_root_count,
            "public_key_count":  report.public_key_count,
            "first_broken_row":  report.first_broken_row_id,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail}
                for c in report.checks
            ],
        }
        print(json.dumps(out, indent=2))
    else:
        print(report.render(verbose=args.verbose))

    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
