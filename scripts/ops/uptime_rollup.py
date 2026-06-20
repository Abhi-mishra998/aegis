#!/usr/bin/env python3
"""Sprint EI-14 — aggregate the trailing 30-day green-day rate.

Walks the per-day artefacts the nightly_verify workflow archives at
``s3://aegis-public-roots-628478946931/nightly/<YYYY-MM-DD>.json`` (one
JSON per day from EI-4 + EI-13). A day is "green" iff every status
field is one of {"pass", "verified", "success"}. Missing days are
counted as "no_data" — they don't help or hurt the rate.

Output JSON shape (uploaded to ``s3://…/uptime/30day.json``):

  {
    "window_start_utc": "2026-05-22",
    "window_end_utc":   "2026-06-20",
    "total_days":  30,
    "green_days":  28,
    "incident_days": 1,
    "no_data_days": 1,
    "green_pct":   93.33,
    "computed_at_utc": "2026-06-20T05:13:00Z",
    "days": [
      {"date": "2026-05-22", "state": "green",    "checks": {…}},
      …
    ]
  }

Backstops the SLA template's 99.5% / 99.9% commitments with a public,
fetch-from-anywhere number. Status page consumes the rollup JSON
directly.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

CHECK_FIELDS = ("aevf_v1_v6", "isolation", "public_probe", "sbom_cve", "chaos")
OK_VALUES   = {"pass", "verified", "success"}


@dataclass
class DayState:
    date_str: str
    state: str               # "green" | "incident" | "no_data"
    checks: dict             # whatever the day's verify.json carried
    source: str              # "local" | "s3" | "missing"


def classify(verify_json: dict | None) -> str:
    """Map a verify.json to "green" / "incident" / "no_data"."""
    if not verify_json:
        return "no_data"
    seen = 0
    for field in CHECK_FIELDS:
        v = verify_json.get(field)
        if v is None:
            continue
        if str(v).lower() in OK_VALUES:
            seen += 1
            continue
        # Any non-OK status (including 'skip' for a check that ran but had
        # no data to compare against) trips the day. Conservative — better
        # to look amber when a check skipped than claim 100% green.
        return "incident"
    return "green" if seen > 0 else "no_data"


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def aggregate(input_dir: Path, *, window_days: int = 30,
              today: date | None = None) -> dict:
    """Read up to ``window_days`` per-day JSON files from input_dir.

    File names are expected to match ``<YYYY-MM-DD>.json``. Missing
    files are reported as no_data days in the output.
    """
    today = today or datetime.now(UTC).date()
    start = today - timedelta(days=window_days - 1)

    days: list[DayState] = []
    for i in range(window_days):
        d = start + timedelta(days=i)
        path = input_dir / f"{d.isoformat()}.json"
        verify = _load(path)
        days.append(DayState(
            date_str=d.isoformat(),
            state=classify(verify),
            checks={k: verify.get(k) for k in CHECK_FIELDS} if verify else {},
            source="local" if path.exists() else "missing",
        ))

    green = sum(1 for d in days if d.state == "green")
    incident = sum(1 for d in days if d.state == "incident")
    no_data = sum(1 for d in days if d.state == "no_data")
    measured = green + incident   # no_data days don't count in the rate

    # green_pct uses MEASURED days as the denominator so a fresh deploy
    # (every day = no_data) shows N/A rather than a misleading 0%.
    pct = (100.0 * green / measured) if measured else 0.0

    return {
        "window_start_utc": days[0].date_str,
        "window_end_utc":   days[-1].date_str,
        "window_days":      window_days,
        "total_days":       len(days),
        "green_days":       green,
        "incident_days":    incident,
        "no_data_days":     no_data,
        "measured_days":    measured,
        "green_pct":        round(pct, 2),
        "computed_at_utc":  datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "days": [
            {"date": d.date_str, "state": d.state, "checks": d.checks}
            for d in days
        ],
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="uptime_rollup",
        description="Aggregate trailing N-day green-day rate from per-day verify.json files.",
    )
    p.add_argument("--input-dir", required=True, type=Path,
                   help="Directory holding <YYYY-MM-DD>.json per-day archives.")
    p.add_argument("--out", required=True, type=Path,
                   help="Where to write the rollup JSON.")
    p.add_argument("--window-days", type=int, default=30,
                   help="Window size in days (default 30).")
    args = p.parse_args(argv)

    if not args.input_dir.is_dir():
        print(f"FAIL — input dir not found: {args.input_dir}", file=sys.stderr)
        return 2

    rollup = aggregate(args.input_dir, window_days=args.window_days)
    args.out.write_text(json.dumps(rollup, indent=2))
    print(
        f"window={rollup['window_start_utc']}..{rollup['window_end_utc']} "
        f"green={rollup['green_days']}/{rollup['measured_days']} "
        f"({rollup['green_pct']}%) no_data={rollup['no_data_days']}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
