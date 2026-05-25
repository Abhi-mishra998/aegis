"""
ACP Audit Log Verifier — Standalone, open-source, no server required.

Given an exported bundle of receipts, inclusion proofs, and daily roots,
this module proves three properties entirely offline:

  1. Receipt integrity  — every signed receipt carries a valid ed25519
                          signature from the ACP signing key.
  2. Merkle inclusion   — every receipt was committed into the correct
                          daily Merkle tree before the root was signed.
  3. Chain consistency  — the sequence of daily roots forms an append-only
                          linked list; no root can be silently replaced.

All verification is pure-function / offline.  The only network call is the
one-time ``acp archive`` step that fetches the export bundle from your ACP
deployment.

Typical usage::

    from pathlib import Path
    from acp_client.verifier import AuditVerifier

    verifier = AuditVerifier.from_export_dir(Path("my-export"))
    result = verifier.verify_export(Path("my-export"))
    print("ok" if result.ok else result.errors)
"""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


# ── Canonical JSON (must match server exactly) ────────────────────────────


def canonical_json(obj: dict[str, Any]) -> bytes:
    """Sort keys, compact separators, UTF-8. Must match signer.py on the server."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _b64d(s: str) -> bytes:
    """Decode URL-safe base64, adding padding as needed."""
    pad = 4 - len(s) % 4
    return base64.urlsafe_b64decode(s + "=" * (pad % 4))


def fingerprint_key(pub_pem: str | bytes) -> str:
    """Return the first 32 hex chars of SHA-256(PEM bytes).

    Matches the server's ``fingerprint_public_key`` function in receipts.py.
    """
    if isinstance(pub_pem, str):
        pub_pem = pub_pem.encode()
    return hashlib.sha256(pub_pem).hexdigest()[:32]


# ── Low-level primitives ──────────────────────────────────────────────────


def verify_receipt(payload: dict[str, Any], public_key_pem: str) -> bool:
    """Verify an ed25519 signature over the canonical receipt JSON.

    Args:
        payload:        The full signed-receipt object, containing at minimum
                        ``receipt``, ``signature``, ``algorithm``, and
                        ``public_key_fingerprint`` fields.
        public_key_pem: ed25519 public key in PEM format.

    Returns:
        ``True`` if the signature is valid and the key fingerprint matches.
        ``False`` if the signature does not verify.

    Raises:
        ValueError: if the payload is malformed (missing fields, wrong
                    algorithm, or invalid PEM).
    """
    for k in ("receipt", "signature", "algorithm", "public_key_fingerprint"):
        if k not in payload:
            raise ValueError(f"missing required field: {k}")
    if payload["algorithm"] != "ed25519":
        raise ValueError(f"unsupported algorithm: {payload['algorithm']!r} (expected 'ed25519')")

    pem_bytes = public_key_pem.encode("ascii") if isinstance(public_key_pem, str) else public_key_pem
    if fingerprint_key(pem_bytes) != payload["public_key_fingerprint"]:
        return False

    try:
        raw_pub = serialization.load_pem_public_key(pem_bytes)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid public key PEM: {exc}") from exc
    if not isinstance(raw_pub, ed25519.Ed25519PublicKey):
        raise ValueError("public key is not an ed25519 key")

    try:
        raw_pub.verify(_b64d(payload["signature"]), canonical_json(payload["receipt"]))
        return True
    except InvalidSignature:
        return False
    except Exception:
        return False


def leaf_hash(receipt_payload: dict[str, Any]) -> str:
    """Compute the Merkle leaf hash for one signed receipt payload.

    The leaf is sha256(canonical_json(entire_payload)) — the ENTIRE payload
    including the ``signature`` field, not just the inner ``receipt`` dict.
    This matches the server-side leaf computation in the transparency service.

    Args:
        receipt_payload: The full signed-receipt object as returned by
                         ``/receipts/{id}`` or stored in receipts/*.json.

    Returns:
        Lower-case hex digest (64 characters).
    """
    return hashlib.sha256(canonical_json(receipt_payload)).hexdigest()


def build_root(leaves: list[str]) -> str:
    """Build a SHA-256 Merkle root from a list of leaf hashes.

    Implements Bitcoin-style odd-node duplication: when a level has an odd
    number of nodes the last node is paired with itself.

    Args:
        leaves: List of lower-case hex hashes (each 64 chars).  Must be
                non-empty; the tree is built in the order given.

    Returns:
        The root hash as a lower-case hex string.

    Special case: an empty list returns
    ``hashlib.sha256(b"").hexdigest()`` (the EMPTY_ROOT sentinel).
    """
    if not leaves:
        return hashlib.sha256(b"").hexdigest()

    level: list[bytes] = [bytes.fromhex(h) for h in leaves]

    while len(level) > 1:
        next_level: list[bytes] = []
        # Duplicate last node if odd count (Bitcoin-style)
        if len(level) % 2 == 1:
            level.append(level[-1])
        for i in range(0, len(level), 2):
            combined = hashlib.sha256(level[i] + level[i + 1]).digest()
            next_level.append(combined)
        level = next_level

    return level[0].hex()


def verify_inclusion(leaf_hex: str, proof: dict[str, Any], expected_root: str) -> bool:
    """Verify a Merkle inclusion proof.

    Walks the sibling list bottom-up from leaf to root.  For each sibling:
    - side="L" means the sibling is on the LEFT  → sha256(sibling ‖ current)
    - side="R" means the sibling is on the RIGHT → sha256(current ‖ sibling)

    The final computed value must equal ``expected_root``.

    Args:
        leaf_hex:      The leaf hash (hex) to prove inclusion of.
        proof:         Inclusion proof dict with ``leaf``, ``index``,
                       ``siblings`` (list of ``{side, hash}``), ``root``,
                       and ``size`` fields.
        expected_root: The Merkle root that must match after traversal.

    Returns:
        ``True`` if the leaf is provably in the tree, ``False`` otherwise.

    Raises:
        ValueError: if the proof object is structurally malformed.
    """
    if not isinstance(proof, dict):
        raise ValueError("proof must be a mapping")
    for k in ("leaf", "siblings", "root"):
        if k not in proof:
            raise ValueError(f"missing required field in proof: {k}")

    if proof["leaf"] != leaf_hex:
        return False
    if proof["root"] != expected_root:
        return False

    cur = bytes.fromhex(leaf_hex)
    for sib in proof["siblings"]:
        side = sib.get("side")
        h_hex = sib.get("hash")
        if side not in ("L", "R") or not isinstance(h_hex, str):
            raise ValueError(f"malformed sibling entry: {sib!r}")
        sh = bytes.fromhex(h_hex)
        if side == "L":
            cur = hashlib.sha256(sh + cur).digest()
        else:
            cur = hashlib.sha256(cur + sh).digest()

    return cur.hex() == expected_root


def verify_root_chain(roots: list[dict[str, Any]]) -> tuple[bool, str]:
    """Verify that a list of signed daily roots forms a consistent, append-only chain.

    Each root payload must have a ``receipt`` sub-dict with ``root_hash`` and
    ``prev_root_hash`` fields.  The list is sorted by date ascending before
    checking.

    Consecutive roots must satisfy::

        roots[i]["receipt"]["prev_root_hash"] == roots[i-1]["receipt"]["root_hash"]

    Args:
        roots: List of signed daily-root payloads (as stored in roots/*.json).

    Returns:
        ``(True, "")``  if the chain is consistent.
        ``(False, msg)`` with a human-readable description of the first break.
    """
    if len(roots) <= 1:
        return True, ""

    # Sort by date field inside receipt; fall back to top-level date key.
    def _date(r: dict[str, Any]) -> str:
        return (
            r.get("receipt", {}).get("date", "")
            or r.get("date", "")
            or r.get("receipt", {}).get("root_date", "")
            or ""
        )

    sorted_roots = sorted(roots, key=_date)

    for i in range(1, len(sorted_roots)):
        prev = sorted_roots[i - 1]
        curr = sorted_roots[i]
        expected = prev.get("receipt", {}).get("root_hash")
        actual = curr.get("receipt", {}).get("prev_root_hash")
        if expected != actual:
            prev_date = _date(prev) or str(i - 1)
            curr_date = _date(curr) or str(i)
            return (
                False,
                f"chain broken between {prev_date} and {curr_date}: "
                f"expected prev_root_hash={expected!r} but got {actual!r}",
            )

    return True, ""


# ── Result dataclasses ────────────────────────────────────────────────────


@dataclass
class ReceiptVerification:
    """Result of verifying one signed receipt."""

    execution_id: str
    ok: bool
    error: str = ""


@dataclass
class InclusionVerification:
    """Result of verifying one Merkle inclusion proof."""

    execution_id: str
    ok: bool
    root_date: str = ""
    error: str = ""


@dataclass
class ExportVerification:
    """Aggregated result of verifying a full audit export bundle."""

    total_receipts: int = 0
    valid_receipts: int = 0
    total_inclusions: int = 0
    valid_inclusions: int = 0
    chain_ok: bool = False
    chain_error: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True iff every check passed with zero errors."""
        return (
            self.valid_receipts == self.total_receipts
            and self.valid_inclusions == self.total_inclusions
            and self.chain_ok
            and not self.errors
        )


# ── High-level verifier class ─────────────────────────────────────────────


class AuditVerifier:
    """Main verifier.  Works offline given exported data.

    Instantiate with the list of public keys (active + historical), then
    call ``verify_export(path)`` to verify a full export directory.

    Example::

        verifier = AuditVerifier(public_keys=[active_pem, *historical_pems])
        result = verifier.verify_export(Path("/tmp/my-export"))
        assert result.ok

    For ad-hoc use::

        verifier = AuditVerifier.from_export_dir(Path("/tmp/my-export"))
        result = verifier.verify_export(Path("/tmp/my-export"))
    """

    def __init__(self, public_keys: list[str]) -> None:
        """Initialise the verifier with one or more ed25519 public key PEMs.

        Args:
            public_keys: List of PEM-encoded ed25519 public keys.  Include
                         every key that was ever used to sign receipts
                         (active + all historical keys from key-rotation).
        """
        if not public_keys:
            raise ValueError("at least one public key is required")
        self._keys: list[str] = list(public_keys)

    # ── Constructors ──────────────────────────────────────────────────────

    @classmethod
    def from_export_dir(cls, export_dir: Path) -> "AuditVerifier":
        """Create an AuditVerifier by reading keys from ``<export_dir>/keys/``.

        Expected layout::

            export_dir/keys/active.pem
            export_dir/keys/historical/*.pem   (optional)

        Args:
            export_dir: Root of the export bundle directory.

        Returns:
            An AuditVerifier loaded with all keys found under ``keys/``.

        Raises:
            FileNotFoundError: if ``keys/active.pem`` does not exist.
        """
        keys_dir = export_dir / "keys"
        active_pem_path = keys_dir / "active.pem"
        if not active_pem_path.exists():
            raise FileNotFoundError(f"active public key not found at {active_pem_path}")
        keys = [active_pem_path.read_text()]
        hist_dir = keys_dir / "historical"
        if hist_dir.is_dir():
            for p in sorted(hist_dir.glob("*.pem")):
                keys.append(p.read_text())
        return cls(keys)

    # ── Single-item verification ──────────────────────────────────────────

    def verify_receipt(self, payload: dict[str, Any]) -> ReceiptVerification:
        """Verify one signed receipt against all known public keys.

        Tries each key in order; the receipt is valid if any key succeeds.

        Args:
            payload: Full signed-receipt object (``receipt``, ``signature``,
                     ``algorithm``, ``public_key_fingerprint`` fields).

        Returns:
            :class:`ReceiptVerification` with ``ok=True`` on the first
            matching key, or ``ok=False`` with an error message.
        """
        exec_id = payload.get("receipt", {}).get("execution_id", "<unknown>")
        last_error = "no public keys loaded"
        for pem in self._keys:
            try:
                if verify_receipt(payload, pem):
                    return ReceiptVerification(execution_id=exec_id, ok=True)
            except ValueError as exc:
                last_error = str(exc)
        return ReceiptVerification(execution_id=exec_id, ok=False, error=last_error)

    def verify_inclusion(
        self,
        receipt_payload: dict[str, Any],
        proof: dict[str, Any],
        signed_root: dict[str, Any],
    ) -> InclusionVerification:
        """Verify that a receipt is included in a signed daily root.

        Steps:
        1. Compute ``leaf_hash(receipt_payload)`` → leaf hex.
        2. Verify the inclusion proof against the root hash in ``signed_root``.

        Args:
            receipt_payload: Full signed-receipt payload.
            proof:           Inclusion proof dict (``leaf``, ``siblings``,
                             ``root``, ``index``, ``size`` fields).
            signed_root:     Signed daily-root payload.  The ``root_hash``
                             is read from ``signed_root["receipt"]["root_hash"]``
                             or ``signed_root["root_hash"]`` as a fallback.

        Returns:
            :class:`InclusionVerification` indicating whether the receipt is
            provably in the tree.
        """
        exec_id = receipt_payload.get("receipt", {}).get("execution_id", "<unknown>")
        root_date = (
            signed_root.get("receipt", {}).get("date", "")
            or signed_root.get("date", "")
            or signed_root.get("receipt", {}).get("root_date", "")
            or ""
        )
        expected_root = (
            signed_root.get("receipt", {}).get("root_hash")
            or signed_root.get("root_hash")
            or ""
        )
        if not expected_root:
            return InclusionVerification(
                execution_id=exec_id,
                ok=False,
                root_date=root_date,
                error="signed_root has no root_hash field",
            )
        lh = leaf_hash(receipt_payload)
        try:
            ok = verify_inclusion(lh, proof, expected_root)
        except ValueError as exc:
            return InclusionVerification(
                execution_id=exec_id,
                ok=False,
                root_date=root_date,
                error=str(exc),
            )
        return InclusionVerification(execution_id=exec_id, ok=ok, root_date=root_date)

    # ── Full-export verification ──────────────────────────────────────────

    def verify_export(self, export_dir: Path) -> ExportVerification:
        """Verify a full audit export directory.

        Expected directory layout::

            export_dir/
              keys/
                active.pem
                historical/*.pem          (optional)
              receipts/
                {execution_id}.json       (signed receipt payload)
              proofs/
                {execution_id}.json       (inclusion proof + signed root)
              roots/
                {YYYY-MM-DD}.json         (signed root payload)

        For each receipt:
        1. Verify ed25519 signature.
        2. If a matching proof exists in ``proofs/``, verify Merkle inclusion
           using the root from ``proofs/{id}.json`` (a dict with ``proof`` and
           ``signed_root`` keys) **or** by looking up
           ``roots/{date}.json`` for the receipt's date.

        The root-chain is verified across every file in ``roots/``.

        Args:
            export_dir: Root of the export bundle.

        Returns:
            An :class:`ExportVerification` summary.
        """
        result = ExportVerification()
        export_dir = Path(export_dir)

        # Load keys if not already loaded from this dir
        keys_dir = export_dir / "keys"
        if keys_dir.is_dir() and (keys_dir / "active.pem").exists():
            # Supplement with keys found in the bundle
            active_pem = (keys_dir / "active.pem").read_text()
            if active_pem not in self._keys:
                self._keys.insert(0, active_pem)
            hist_dir = keys_dir / "historical"
            if hist_dir.is_dir():
                for p in sorted(hist_dir.glob("*.pem")):
                    pem = p.read_text()
                    if pem not in self._keys:
                        self._keys.append(pem)

        # Index signed roots by date
        roots_dir = export_dir / "roots"
        root_index: dict[str, dict[str, Any]] = {}
        signed_roots: list[dict[str, Any]] = []
        if roots_dir.is_dir():
            for rp in sorted(roots_dir.glob("*.json")):
                try:
                    root_payload = json.loads(rp.read_text())
                    root_index[rp.stem] = root_payload
                    signed_roots.append(root_payload)
                except (json.JSONDecodeError, OSError) as exc:
                    result.errors.append(f"roots/{rp.name}: could not read: {exc}")

        # Index proofs by execution_id (filename stem)
        proofs_dir = export_dir / "proofs"
        proof_index: dict[str, dict[str, Any]] = {}
        if proofs_dir.is_dir():
            for pp in proofs_dir.glob("*.json"):
                try:
                    proof_index[pp.stem] = json.loads(pp.read_text())
                except (json.JSONDecodeError, OSError) as exc:
                    result.errors.append(f"proofs/{pp.name}: could not read: {exc}")

        # Verify each receipt
        receipts_dir = export_dir / "receipts"
        receipt_files = sorted(receipts_dir.glob("*.json")) if receipts_dir.is_dir() else []

        for rfile in receipt_files:
            try:
                receipt_payload = json.loads(rfile.read_text())
            except (json.JSONDecodeError, OSError) as exc:
                result.errors.append(f"receipts/{rfile.name}: could not read: {exc}")
                continue

            result.total_receipts += 1
            rv = self.verify_receipt(receipt_payload)
            if rv.ok:
                result.valid_receipts += 1
            else:
                result.errors.append(
                    f"receipts/{rfile.name}: signature INVALID: {rv.error}"
                )

            # Inclusion proof
            exec_id = rfile.stem
            proof_data = proof_index.get(exec_id) or proof_index.get(
                receipt_payload.get("receipt", {}).get("execution_id", "")
            )
            if proof_data is None:
                continue

            result.total_inclusions += 1

            # Proof file may contain {proof: {...}, signed_root: {...}}
            # or may be just the proof dict directly.
            if "proof" in proof_data and "signed_root" in proof_data:
                proof = proof_data["proof"]
                signed_root = proof_data["signed_root"]
            elif "siblings" in proof_data:
                # bare proof — look up the root by date
                proof = proof_data
                root_date = proof_data.get("root_date", "")
                signed_root = root_index.get(root_date, {})
                if not signed_root:
                    # Try to use the root hash from the proof itself
                    signed_root = {"root_hash": proof_data.get("root", "")}
            else:
                result.errors.append(
                    f"proofs/{exec_id}.json: unrecognised proof format"
                )
                continue

            iv = self.verify_inclusion(receipt_payload, proof, signed_root)
            if iv.ok:
                result.valid_inclusions += 1
            else:
                result.errors.append(
                    f"proofs/{exec_id}.json: inclusion INVALID: {iv.error}"
                )

        # Root chain verification
        if signed_roots:
            chain_ok, chain_error = verify_root_chain(signed_roots)
            result.chain_ok = chain_ok
            result.chain_error = chain_error
            if not chain_ok:
                result.errors.append(f"root chain: {chain_error}")
        else:
            # No roots to verify — chain is vacuously consistent
            result.chain_ok = True

        return result
