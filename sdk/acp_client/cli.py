"""acp — CLI for the ACP SDK.

Subcommands:
    acp validate <path>                              Validate a policy file.
    acp version                                      Print SDK version.

Offline verification commands (no network, no ACP install required by the
verifier — works entirely from archived bundles):

    acp verify-receipt   <receipt.json> --pubkey <pem>
    acp verify-inclusion <inclusion.json>            (root is inside the proof)
    acp verify-bundle    <dir>                       Whole-dir verification.

All verify-* commands exit 0 if everything checks out, 1 if any signature,
fingerprint, or Merkle proof fails. Pass --json for machine-readable output.

Wired via [project.scripts] in pyproject.toml as `acp`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .archive import ArchiveError, build_archive
from .errors import PolicyError
from .init_project import init_project
from .policy import load_policy
from .receipts import verify_receipt
from .transparency import leaf_hash_for_receipt, verify_inclusion


# ─── Existing commands ────────────────────────────────────────────────────


def _cmd_validate(args: argparse.Namespace) -> int:
    path = Path(args.path)
    try:
        policy = load_policy(path)
    except PolicyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"ok: {path}")
    print(f"  version:  {policy.version}")
    print(f"  agent:    {policy.agent}")
    print(f"  allow:    {len(policy.allow)} rule(s)")
    print(f"  deny:     {len(policy.deny)} rule(s)")
    if policy.autonomy.max_actions_per_minute is not None:
        print(f"  autonomy.max_actions_per_minute: {policy.autonomy.max_actions_per_minute}")
    if policy.autonomy.require_approval_for:
        print(f"  autonomy.require_approval_for:   {policy.autonomy.require_approval_for}")
    return 0


def _cmd_version(_args: argparse.Namespace) -> int:
    print(f"acp {__version__}")
    return 0


# ─── Verification helpers ─────────────────────────────────────────────────


def _load_json(path: Path) -> Any:
    try:
        with open(path) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise SystemExit(f"error: {path}: invalid JSON: {e}")
    except FileNotFoundError:
        raise SystemExit(f"error: {path}: not found")


def _emit(result: dict[str, Any], as_json: bool) -> int:
    if as_json:
        print(json.dumps(result, indent=2))
    else:
        for line in result.get("lines", []):
            print(line)
    return 0 if result.get("ok") else 1


# ─── Verify receipt ──────────────────────────────────────────────────────


def _cmd_verify_receipt(args: argparse.Namespace) -> int:
    payload = _load_json(Path(args.receipt))
    try:
        pub_pem = Path(args.pubkey).read_text()
    except FileNotFoundError:
        raise SystemExit(f"error: {args.pubkey}: public key not found")

    try:
        ok = verify_receipt(payload, pub_pem)
    except ValueError as e:
        return _emit({"ok": False, "error": str(e), "lines": [f"error: {e}"]}, args.json)

    exec_id = payload.get("receipt", {}).get("execution_id", "?")
    fp = payload.get("public_key_fingerprint", "?")
    result = {
        "ok": ok,
        "execution_id": exec_id,
        "public_key_fingerprint": fp,
        "lines": [
            f"{'OK' if ok else 'FAIL'}: receipt {exec_id} (fp {fp})",
        ],
    }
    return _emit(result, args.json)


# ─── Verify inclusion ─────────────────────────────────────────────────────


def _cmd_verify_inclusion(args: argparse.Namespace) -> int:
    payload = _load_json(Path(args.inclusion))

    proof = payload.get("proof") or payload
    root = args.root or proof.get("root")
    leaf = args.leaf or proof.get("leaf")
    if not root or not leaf:
        return _emit(
            {"ok": False, "lines": ["error: --root and --leaf required when not present in proof"]},
            args.json,
        )

    try:
        ok = verify_inclusion(leaf, proof, root)
    except ValueError as e:
        return _emit({"ok": False, "error": str(e), "lines": [f"error: {e}"]}, args.json)

    result = {
        "ok": ok,
        "root": root,
        "leaf": leaf,
        "index": proof.get("index"),
        "size": proof.get("size"),
        "lines": [
            f"{'OK' if ok else 'FAIL'}: inclusion of leaf at index {proof.get('index')} of {proof.get('size')} → root {root[:16]}…",
        ],
    }
    return _emit(result, args.json)


# ─── Verify bundle ────────────────────────────────────────────────────────


def _cmd_init(args: argparse.Namespace) -> int:
    try:
        result = init_project(target_dir=args.dir, agent_id=args.agent_id, force=args.force)
    except (FileNotFoundError, NotADirectoryError, ValueError) as e:
        return _emit({"ok": False, "error": str(e), "lines": [f"error: {e}"]}, args.json)

    lines: list[str] = []
    for p in result.created:
        lines.append(f"  created  {p}")
    for p in result.skipped:
        lines.append(f"  skipped  {p} (already exists; pass --force to overwrite)")
    lines.append("")
    if result.created:
        lines.append("Next steps:")
        lines.append("  1. Set ACP_API_KEY and ACP_BASE_URL in your environment")
        lines.append("  2. Customize .acp/policy.yaml for your agent")
        lines.append("  3. Run: acp validate .acp/policy.yaml")
        lines.append("  4. Wire .acp/example.py into your codebase")
    else:
        lines.append("No new files created. Pass --force to overwrite the scaffold.")

    return _emit(
        {
            "ok": True,
            "created": [str(p) for p in result.created],
            "skipped": [str(p) for p in result.skipped],
            "lines": lines,
        },
        args.json,
    )


def _cmd_archive(args: argparse.Namespace) -> int:
    try:
        counts = build_archive(
            base_url=args.base_url,
            token=args.token,
            out_dir=args.out,
            tenant=args.tenant,
            since=args.since,
            until=args.until,
            limit=args.limit,
        )
    except ArchiveError as e:
        return _emit({"ok": False, "error": str(e), "lines": [f"error: {e}"]}, args.json)

    result = {
        "ok": True,
        "out": args.out,
        "counts": counts,
        "lines": [
            f"archive: {args.out}",
            f"  receipts written:  {counts['receipts']}",
            f"  inclusion proofs:  {counts['inclusion']}",
            f"  daily roots:       {counts['roots']}",
            "",
            f"Verify with:  acp verify-bundle {args.out}",
        ],
    }
    return _emit(result, args.json)


def _cmd_verify_bundle(args: argparse.Namespace) -> int:
    """Verify a directory of archived ACP artifacts.

    Expected layout:
        <bundle>/public_key.pem
        <bundle>/receipts/*.json     — signed-receipt payloads
        <bundle>/inclusion/*.json    — inclusion proofs (paired with receipts)
        <bundle>/roots/*.json        — signed daily-root commitments (optional)

    For every receipt, we verify:
      1. The ed25519 signature on the receipt itself.
      2. If a matching inclusion proof exists, that the receipt's leaf is in
         the Merkle tree, AND that the proof's root matches the signed root
         (if a roots/<date>.json file exists).
    """
    bundle = Path(args.bundle)
    if not bundle.is_dir():
        return _emit({"ok": False, "lines": [f"error: {bundle}: not a directory"]}, args.json)

    pubkey_path = bundle / "public_key.pem"
    if not pubkey_path.exists():
        return _emit({"ok": False, "lines": [f"error: {pubkey_path}: not found"]}, args.json)
    pub_pem = pubkey_path.read_text()

    receipts_dir = bundle / "receipts"
    inclusion_dir = bundle / "inclusion"
    roots_dir = bundle / "roots"

    receipt_files = sorted(receipts_dir.glob("*.json")) if receipts_dir.is_dir() else []
    if not receipt_files:
        return _emit({"ok": False, "lines": [f"error: no receipts under {receipts_dir}"]}, args.json)

    # Index inclusion proofs by execution_id (filename without extension).
    inclusion_index: dict[str, Path] = {}
    if inclusion_dir.is_dir():
        for p in inclusion_dir.glob("*.json"):
            inclusion_index[p.stem] = p

    # Index signed roots by date.
    root_index: dict[str, Path] = {}
    if roots_dir.is_dir():
        for p in roots_dir.glob("*.json"):
            root_index[p.stem] = p

    counts = {
        "receipts": 0,
        "receipts_ok": 0,
        "inclusion_checked": 0,
        "inclusion_ok": 0,
        "root_anchored": 0,
        "root_matches": 0,
    }
    failures: list[str] = []
    lines: list[str] = []

    for rfile in receipt_files:
        counts["receipts"] += 1
        payload = _load_json(rfile)
        exec_id = payload.get("receipt", {}).get("execution_id", rfile.stem)

        # 1. signature
        try:
            ok = verify_receipt(payload, pub_pem)
        except ValueError as e:
            failures.append(f"{rfile}: malformed receipt: {e}")
            continue
        if not ok:
            failures.append(f"{rfile}: signature INVALID")
            continue
        counts["receipts_ok"] += 1

        # 2. inclusion proof if available
        ipath = inclusion_index.get(rfile.stem) or inclusion_index.get(exec_id)
        if ipath is None:
            lines.append(f"  OK   receipt   {exec_id} (no inclusion proof archived)")
            continue
        counts["inclusion_checked"] += 1

        inclusion_payload = _load_json(ipath)
        proof = inclusion_payload.get("proof") or inclusion_payload
        leaf = leaf_hash_for_receipt(payload)
        try:
            inc_ok = verify_inclusion(leaf, proof, proof.get("root"))
        except ValueError as e:
            failures.append(f"{ipath}: malformed proof: {e}")
            continue
        if not inc_ok:
            failures.append(f"{ipath}: inclusion proof INVALID")
            continue
        counts["inclusion_ok"] += 1

        # 3. cross-check against a signed root for the same date if archived.
        root_date = inclusion_payload.get("root_date") or ""
        root_file = root_index.get(root_date)
        if root_file:
            counts["root_anchored"] += 1
            root_payload = _load_json(root_file)
            signed = root_payload.get("signed") or root_payload
            if signed.get("receipt", {}).get("root_hash") == proof.get("root"):
                counts["root_matches"] += 1
                lines.append(f"  OK   anchored {exec_id} → root {proof['root'][:16]}… ({root_date})")
            else:
                failures.append(f"{ipath}: proof root != signed daily root for {root_date}")
        else:
            lines.append(f"  OK   included {exec_id} (no signed daily root archived for {root_date or '?'})")

    ok = not failures
    lines = [
        f"bundle: {bundle}",
        f"  receipts:             {counts['receipts_ok']}/{counts['receipts']}",
        f"  inclusion proofs:     {counts['inclusion_ok']}/{counts['inclusion_checked']}",
        f"  daily-root anchored:  {counts['root_matches']}/{counts['root_anchored']}",
    ] + lines

    if failures:
        lines.append("")
        lines.append("FAILURES:")
        lines += [f"  - {f}" for f in failures]
    lines.append("")
    lines.append("OK" if ok else "FAIL")

    return _emit({"ok": ok, "counts": counts, "failures": failures, "lines": lines}, args.json)


# ─── argparse wiring ──────────────────────────────────────────────────────


def _cmd_verify_chain(args: argparse.Namespace) -> int:
    """Pull /audit/export NDJSON and re-derive every event_hash. Reports any
    tampering as a non-zero exit code so CI / cron can alert on drift.

    The recomputation uses the same canonical hash function the audit writer
    used at insert time (`sdk.common.audit_hash.compute_event_hash`) so a
    mismatch means either:
      (a) the row was edited at the database level after insert, or
      (b) the audit writer changed its hash recipe without a migration.
    Either way the chain is broken and the operator must investigate.
    """
    import json as _json
    import httpx
    from sdk.common.audit_hash import GENESIS_HASH, compute_event_hash

    base = args.base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {args.token}", "User-Agent": "acp-cli/verify-chain"}
    if args.tenant:
        headers["X-Tenant-ID"] = args.tenant
    params: dict[str, object] = {"limit": args.limit}
    if args.since: params["since"] = args.since
    if args.until: params["until"] = args.until

    # /audit/export is newest-first NDJSON. To verify the chain we need
    # oldest-first; collect rows, sort, then walk per shard.
    rows: list[dict] = []
    try:
        with httpx.Client(base_url=base, timeout=args.timeout, headers=headers) as http:
            with http.stream("GET", "/audit/export", params=params) as resp:
                if resp.status_code != 200:
                    return _emit(
                        {"valid": False, "reason": f"http_{resp.status_code}", "detail": resp.read().decode(errors="replace")[:200]},
                        args.json,
                    )
                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        rows.append(_json.loads(line))
                    except Exception:
                        continue
    except httpx.HTTPError as exc:
        return _emit({"valid": False, "reason": "network", "detail": str(exc)}, args.json)

    # Group by chain_shard and verify each independently.
    rows.sort(key=lambda r: (int(r.get("chain_shard") or 0), r.get("timestamp") or "", r.get("id") or ""))

    violations: list[dict] = []
    last_hash: dict[int, str] = {}
    processed = 0
    for r in rows:
        processed += 1
        shard = int(r.get("chain_shard") or 0)
        expected_prev = last_hash.get(shard, GENESIS_HASH)
        recomputed = compute_event_hash(
            prev_hash=str(r.get("prev_hash") or GENESIS_HASH),
            tenant_id=str(r.get("tenant_id") or ""),
            agent_id=str(r.get("agent_id") or ""),
            action=r.get("action") or "",
            tool=r.get("tool"),
            decision=r.get("decision") or "",
            request_id=r.get("request_id"),
        )
        if r.get("prev_hash") != expected_prev:
            violations.append({
                "request_id": r.get("request_id"),
                "shard": shard,
                "kind": "chain_gap",
                "expected_prev": expected_prev,
                "actual_prev": r.get("prev_hash"),
            })
        if recomputed != r.get("event_hash"):
            violations.append({
                "request_id": r.get("request_id"),
                "shard": shard,
                "kind": "hash_tamper",
                "expected_hash": recomputed,
                "stored_hash": r.get("event_hash"),
            })
        last_hash[shard] = r.get("event_hash") or expected_prev

    return _emit({
        "valid": not violations,
        "processed": processed,
        "shards": sorted(last_hash.keys()),
        "violations": violations[:50],  # cap for human readability
        "total_violations": len(violations),
    }, args.json)


def _cmd_verify_root(args: argparse.Namespace) -> int:
    """Verify a signed transparency root locally + optionally fetch the
    consistency chain from the deployment and confirm it is append-only.

    Modes:
      `--root path/to/root.json`  — verify the signature of a signed root
      `--from YYYY-MM-DD --to YYYY-MM-DD --base-url … --token …`
                                  — fetch the consistency chain and validate
    """
    import httpx
    if args.root:
        try:
            payload = _load_json(Path(args.root))
        except Exception as exc:
            return _emit({"valid": False, "reason": "io", "detail": str(exc)}, args.json)
        # Bundle pubkey lookup: if --pubkey supplied, verify offline; else
        # round-trip to /transparency/verify-root (still cryptographic but
        # requires network).
        if args.pubkey:
            from .transparency import verify_root_signature
            try:
                pem = Path(args.pubkey).read_text()
            except Exception as exc:
                return _emit({"valid": False, "reason": "io", "detail": str(exc)}, args.json)
            ok = verify_root_signature(payload, pem)
            return _emit({"valid": bool(ok)}, args.json)
        if args.base_url:
            with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=args.timeout,
                              headers={"Authorization": f"Bearer {args.token or ''}"}) as http:
                resp = http.post("/transparency/verify-root", json=payload)
                return _emit(resp.json().get("data") or resp.json(), args.json)
        return _emit({"valid": False, "reason": "no_pubkey_or_endpoint"}, args.json)

    # Chain mode
    if not (args.base_url and args.from_date and args.to_date):
        return _emit({"valid": False, "reason": "missing_args",
                      "detail": "supply --root OR (--base-url --from --to)"}, args.json)
    headers = {"Authorization": f"Bearer {args.token or ''}"}
    if args.tenant: headers["X-Tenant-ID"] = args.tenant
    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=args.timeout, headers=headers) as http:
        resp = http.get("/transparency/consistency",
                        params={"from_date": args.from_date, "to_date": args.to_date})
        if resp.status_code != 200:
            return _emit({"valid": False, "reason": f"http_{resp.status_code}",
                          "detail": resp.text[:200]}, args.json)
        body = resp.json()
        data = body.get("data") if isinstance(body, dict) and "data" in body else body
        return _emit(data, args.json)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="acp", description="ACP SDK CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_validate = sub.add_parser("validate", help="validate a .acp/policy.yaml file")
    p_validate.add_argument("path", help="path to policy file")
    p_validate.set_defaults(func=_cmd_validate)

    p_version = sub.add_parser("version", help="print SDK version")
    p_version.set_defaults(func=_cmd_version)

    p_vr = sub.add_parser("verify-receipt", help="verify a signed receipt JSON")
    p_vr.add_argument("receipt", help="path to receipt JSON (as returned by /v1/receipts/{id})")
    p_vr.add_argument("--pubkey", required=True, help="path to ed25519 public key PEM")
    p_vr.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p_vr.set_defaults(func=_cmd_verify_receipt)

    p_vi = sub.add_parser("verify-inclusion", help="verify a Merkle inclusion proof")
    p_vi.add_argument("inclusion", help="path to inclusion JSON")
    p_vi.add_argument("--root", help="override root hex (else taken from proof)")
    p_vi.add_argument("--leaf", help="override leaf hex (else taken from proof)")
    p_vi.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p_vi.set_defaults(func=_cmd_verify_inclusion)

    p_vb = sub.add_parser("verify-bundle", help="verify a whole bundle directory")
    p_vb.add_argument("bundle", help="bundle directory with public_key.pem + receipts/ + inclusion/ + roots/")
    p_vb.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p_vb.set_defaults(func=_cmd_verify_bundle)

    p_init = sub.add_parser("init", help="scaffold .acp/policy.yaml and .acp/example.py in the current directory")
    p_init.add_argument("--dir", default=".", help="target directory (default: current)")
    p_init.add_argument("--agent-id", default="agent_default", help="agent identifier to pre-fill in the templates")
    p_init.add_argument("--force", action="store_true", help="overwrite existing .acp/ files")
    p_init.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p_init.set_defaults(func=_cmd_init)

    p_ar = sub.add_parser("archive", help="produce a verifiable bundle from a running ACP deployment")
    p_ar.add_argument("--base-url", required=True, help="ACP gateway base URL (e.g. https://acp.example.com)")
    p_ar.add_argument("--token", required=True, help="API bearer token (or set ACP_API_KEY)")
    p_ar.add_argument("--out", required=True, help="output directory for the bundle")
    p_ar.add_argument("--tenant", help="tenant UUID (sent as X-Tenant-ID)")
    p_ar.add_argument("--since", help="ISO-8601 timestamp; lower bound on audit rows")
    p_ar.add_argument("--until", help="ISO-8601 timestamp; upper bound on audit rows")
    p_ar.add_argument("--limit", type=int, default=10_000, help="max rows pulled from /audit/export (default 10000)")
    p_ar.add_argument("--json", action="store_true", help="emit JSON summary instead of human text")
    p_ar.set_defaults(func=_cmd_archive)

    p_vc = sub.add_parser("verify-chain", help="re-derive every event_hash from /audit/export and detect tampering")
    p_vc.add_argument("--base-url", required=True, help="ACP gateway base URL")
    p_vc.add_argument("--token",    required=True, help="API bearer token")
    p_vc.add_argument("--tenant",   help="tenant UUID (X-Tenant-ID header)")
    p_vc.add_argument("--since",    help="ISO-8601 lower bound on timestamp")
    p_vc.add_argument("--until",    help="ISO-8601 upper bound on timestamp")
    p_vc.add_argument("--limit",    type=int, default=10_000, help="max rows to pull (default 10000)")
    p_vc.add_argument("--timeout",  type=float, default=30.0)
    p_vc.add_argument("--json",     action="store_true")
    p_vc.set_defaults(func=_cmd_verify_chain)

    p_vrt = sub.add_parser("verify-root", help="verify a signed transparency root OR the consistency chain")
    p_vrt.add_argument("--root",      help="path to a signed root JSON (offline verify)")
    p_vrt.add_argument("--pubkey",    help="path to root-signing public key PEM (offline mode)")
    p_vrt.add_argument("--base-url",  help="gateway base URL (online mode)")
    p_vrt.add_argument("--token",     help="API bearer token (online mode)")
    p_vrt.add_argument("--tenant",    help="tenant UUID (online mode)")
    p_vrt.add_argument("--from",      dest="from_date", help="older root date (YYYY-MM-DD) for consistency chain")
    p_vrt.add_argument("--to",        dest="to_date",   help="newer root date (YYYY-MM-DD) for consistency chain")
    p_vrt.add_argument("--timeout",   type=float, default=30.0)
    p_vrt.add_argument("--json",      action="store_true")
    p_vrt.set_defaults(func=_cmd_verify_root)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
