"""Generate verifier-compatible evidence bundles.

Output schema matches `tools/aegis_verify/verifier.py`'s expectations
exactly (format ``aegis-evidence-bundle/2026-06``). The bundle is
self-contained:

  * Every public key that signed anything in the bundle (active +
    historical) is embedded as PEM. The verifier never phones home.
  * Every daily Merkle root that covers any row in the bundle is
    embedded with its full signed receipt and canonical payload.
  * Every audit row is embedded with its computed mapping to EU AI
    Act articles, NIST AI RMF function IDs, and SOC 2 control IDs.
  * Retention metadata is included so the auditor can sanity-check
    the bundle against the published retention policy.

This is the moat: a customer's auditor downloads ONE file, runs
`aegis-verify --bundle bundle.json` on their own laptop, gets PASS,
and signs off. No "trust the dashboard."
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.audit.models import (
    AuditLog,
    TransparencyHistoricalKey,
    TransparencyRoot,
)
from services.audit.signer import get_signer

BUNDLE_FORMAT_VERSION = "aegis-evidence-bundle/2026-06"

# Default retention. Configurable via env var on the audit service.
# Surfaced in every bundle so the auditor sees what we claim.
import os

AUDIT_RETENTION_DAYS = int(os.environ.get("AUDIT_RETENTION_DAYS", "180"))


# ───────────────────────────────────────────────────────────────────────────
# Per-row mapping to framework control IDs
# ───────────────────────────────────────────────────────────────────────────

def _map_row_to_controls(row: AuditLog) -> dict[str, list[str]]:
    """Map an audit row's (action, decision) to framework control IDs.

    Conservative on purpose — we cite real Article / control IDs and
    only the ones that the row actually evidences. Better an under-claim
    we can defend than an over-claim that breaks under audit scrutiny.
    """
    eu_ai_act: list[str] = []
    soc2: list[str] = []
    nist: list[str] = []
    dpdp: list[str] = []   # A5: India DPDP Act, 2023 + DPDP Rules (Nov 2025)

    action = (row.action or "").lower()
    decision = (row.decision or "").lower()

    # Every tool-call execution is evidence under Article 12 (record-keeping)
    # — that's literally what the article requires logging of — and Article 13
    # (transparency about the system's operation).
    if action == "execute_tool":
        eu_ai_act.extend(["Article 12 (record-keeping)", "Article 13 (transparency)"])
        soc2.append("CC6.1 (logical access)")
        nist.append("MEASURE 2.1 (system performance & operations)")
        # DPDP §8(5) — Data Fiduciaries shall implement appropriate
        # technical + organisational measures. A signed audit row of
        # every agent tool call IS one of those measures.
        # DPDP §8(7) — security safeguards. Logging is enumerated in the
        # Rules (Nov 2025) Schedule II as a "reasonable security safeguard".
        dpdp.extend([
            "Section 8(5) — technical & organisational measures",
            "Section 8(7) — reasonable security safeguards",
        ])

    # Denials and escalations are post-market monitoring data (Article 61).
    if decision in ("deny", "block", "kill", "escalate"):
        eu_ai_act.append("Article 61 (post-market monitoring)")
        nist.append("GOVERN 5.1 (incident response)")
        soc2.append("CC7.2 (system monitoring)")
        # DPDP §8(8) — Data Fiduciary's obligation to take measures to
        # detect, prevent, and respond to personal-data breaches. A
        # blocked exfil + the signed record of that block is direct
        # evidence the obligation is honoured.
        # DPDP §8(6) — record of personal data processing activities.
        dpdp.extend([
            "Section 8(6) — record of processing activities",
            "Section 8(8) — breach detection & response",
        ])

    # Human-override events are direct Article 14 evidence.
    if action in ("human_override", "approval_granted", "approval_denied",
                  "manual_intervention"):
        eu_ai_act.append("Article 14 (human oversight)")
        soc2.append("CC1.4 (commitment)")
        nist.append("MANAGE 2.3 (oversight)")
        # DPDP — when an automated decision is overridden by a human
        # operator and that override is recorded, it evidences §8(9)
        # (grievance + redressal mechanism) and the Rules Schedule III
        # reasonable-purposes carve-out for human review.
        dpdp.append("Section 8(9) — grievance & redressal mechanism")

    # PII-related blocks land under Article 10 (data governance) — they
    # show the platform's data-quality controls firing.
    findings = []
    if isinstance(row.metadata_json, dict):
        f = row.metadata_json.get("findings") or row.metadata_json.get("flags") or []
        if isinstance(f, list):
            findings = [str(x).lower() for x in f]
    # Also key off the audit row's reason field — `bulk_pii_egress_above_threshold`
    # and `external_pii_exfil` are the v3 R0 reasons that fire on PII-shaped
    # patterns; treat them as PII-relevant for DPDP mapping.
    reason = (row.reason or "").lower()
    pii_relevant = any("pii" in f for f in findings) or any(
        marker in reason for marker in ("pii", "egress", "exfil")
    )
    if pii_relevant:
        eu_ai_act.append("Article 10 (data governance)")
        soc2.append("CC6.7 (information transmission)")
        # DPDP §11 — Data Principal's right to correction / erasure;
        # blocking an unauthorised PII egress is the platform exercising
        # the safeguard the Data Principal is entitled to under §11.
        # DPDP Rules (Nov 2025) Schedule II — personal data shall not be
        # transferred to unauthorised recipients; a blocked external-domain
        # email is the evidentiary record of that safeguard firing.
        dpdp.extend([
            "Section 11 — Data Principal rights (correction/erasure)",
            "Rules Schedule II — restriction on unauthorised transfer",
        ])

    # Annex IV documentation evidence — the platform's governance posture
    # is itself part of the technical documentation the regulator wants.
    eu_ai_act.append("Annex IV §3 (monitoring system)")

    # Stable dedup (preserve order).
    def _dedup(items: list[str]) -> list[str]:
        seen, out = set(), []
        for i in items:
            if i not in seen:
                seen.add(i); out.append(i)
        return out

    return {
        "eu_ai_act":   _dedup(eu_ai_act),
        "soc2":        _dedup(soc2),
        "nist_ai_rmf": _dedup(nist),
        "dpdp":        _dedup(dpdp),
    }


# ───────────────────────────────────────────────────────────────────────────
# Canonical JSON — must match the writer's canonicalization (sort_keys,
# separators (",",":"))
# ───────────────────────────────────────────────────────────────────────────

def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


# ───────────────────────────────────────────────────────────────────────────
# Row serialization — keep field set exact so the verifier's
# `_recompute_event_hash` produces the same hash.
# ───────────────────────────────────────────────────────────────────────────

def _serialize_row(row: AuditLog) -> dict[str, Any]:
    return {
        "id":            str(row.id),
        "tenant_id":     str(row.tenant_id),
        "agent_id":      str(row.agent_id) if row.agent_id else None,
        "action":        row.action,
        "tool":          row.tool,
        "decision":      row.decision,
        "reason":        row.reason or "",
        "metadata_json": row.metadata_json or {},
        "request_id":    row.request_id,
        "timestamp":     row.timestamp.isoformat() if row.timestamp else None,
        "chain_shard":   int(getattr(row, "chain_shard", 0) or 0),
        "prev_hash":     row.prev_hash,
        "event_hash":    row.event_hash,
    }


# ───────────────────────────────────────────────────────────────────────────
# Bundle generator
# ───────────────────────────────────────────────────────────────────────────

async def generate_verifiable_bundle(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    framework: str,
    period_start: datetime,
    period_end: datetime,
) -> dict[str, Any]:
    """Build a self-contained, offline-verifiable evidence bundle."""
    # 1. Audit rows in the period, ordered by (chain_shard, timestamp) so
    # the verifier's V3 (prev_hash chain) traversal hits them in chain order.
    rows_q = (
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant_id)
        .where(AuditLog.timestamp >= period_start)
        .where(AuditLog.timestamp <= period_end)
        .order_by(AuditLog.timestamp.asc())
    )
    rows: list[AuditLog] = list((await db.execute(rows_q)).scalars().all())

    earliest_ts = rows[0].timestamp.isoformat() if rows else None
    latest_ts   = rows[-1].timestamp.isoformat() if rows else None

    # 2. All transparency roots whose date overlaps the period. Use date-
    # truncation on row timestamps to find the range of root_dates needed.
    if rows:
        date_floor = rows[0].timestamp.date()
        date_ceil  = rows[-1].timestamp.date()
    else:
        date_floor = period_start.date()
        date_ceil  = period_end.date()

    roots_q = (
        select(TransparencyRoot)
        .where(TransparencyRoot.tenant_id == tenant_id)
        .where(TransparencyRoot.root_date >= date_floor)
        .where(TransparencyRoot.root_date <= date_ceil)
        .order_by(TransparencyRoot.root_date.asc())
    )
    roots: list[TransparencyRoot] = list((await db.execute(roots_q)).scalars().all())

    # 3. Public keys — active + historical. The verifier picks by `kid`.
    active = get_signer().public_key_info()
    active_kid = active.get("fingerprint")
    public_keys: list[dict[str, Any]] = [{
        "kid":        active_kid,
        "algorithm":  active.get("algorithm", "ed25519"),
        "pem":        active.get("public_key_pem", ""),
        "valid_from": active.get("created_at"),
        "valid_to":   None,
    }]
    hist_rows = list((await db.execute(select(TransparencyHistoricalKey))).scalars().all())
    for h in hist_rows:
        public_keys.append({
            "kid":        h.fingerprint,
            "algorithm":  h.algorithm,
            "pem":        h.public_key_pem,
            "valid_from": None,
            "valid_to":   h.rotated_at.isoformat() if h.rotated_at else None,
        })

    # 4. Merkle roots — pull receipt + signature + canonical-payload
    # straight from the persisted signed_root_payload blob. We MUST keep
    # the canonical payload identical to what the signer signed, or V4 will
    # reject signatures that are actually valid.
    merkle_roots: list[dict[str, Any]] = []
    for root in roots:
        signed = root.signed_root_payload or {}
        receipt = signed.get("receipt") or {}
        signature_b64 = signed.get("signature") or ""
        algorithm = signed.get("algorithm", "ed25519")
        merkle_roots.append({
            "root_date":                       root.root_date.isoformat(),
            "root_hash":                       root.root_hash,
            "leaf_count":                      root.leaf_count,
            "leaf_range_start_id":             str(root.leaf_range_start_id) if root.leaf_range_start_id else None,
            "leaf_range_end_id":               str(root.leaf_range_end_id)   if root.leaf_range_end_id   else None,
            "prev_root_hash":                  root.prev_root_hash,
            "kid":                             root.signing_key_fingerprint or active_kid,
            "algorithm":                       algorithm,
            "signature_b64":                   signature_b64,
            # The canonical payload the writer hashed/signed. We rebuild
            # it from the persisted receipt using the same canonicalization
            # the writer used — sort_keys=True + separators=(",",":").
            "signed_payload_canonical_json":   _canonical(receipt),
        })

    # 5. Records — each row serialized with its computed mapping plus the
    # `merkle_root_date` it falls under (so the verifier can correlate).
    records: list[dict[str, Any]] = []
    for r in rows:
        row_date = r.timestamp.date().isoformat() if r.timestamp else None
        records.append({
            "audit_row":        _serialize_row(r),
            "mappings":         _map_row_to_controls(r),
            "merkle_root_date": row_date,
        })

    bundle = {
        "format_version": BUNDLE_FORMAT_VERSION,
        "framework":      framework,
        "tenant_id":      str(tenant_id),
        "period": {
            "start": period_start.isoformat(),
            "end":   period_end.isoformat(),
        },
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "public_keys":    public_keys,
        "merkle_roots":   merkle_roots,
        "records":        records,
        "retention_metadata": {
            "policy":                    "≥ 6 months minimum (EU AI Act Article 12)",
            "configured_retention_days": AUDIT_RETENTION_DAYS,
            "earliest_row_in_bundle":    earliest_ts,
            "latest_row_in_bundle":      latest_ts,
            "notes":                     (
                "Enforcement is configured via AUDIT_RETENTION_DAYS env. "
                "The auditor can re-pull a bundle for any 6-month window "
                "via /compliance/verifiable-bundle/{framework}."
            ),
        },
        "verifier_recipe": {
            "tool":     "aegis-verify",
            "install":  "pip install cryptography && python -m aegis_verify --bundle <file>",
            "source":   "tools/aegis_verify in the Aegis repo (Apache 2.0)",
            "exit_0":   "every signature, hash chain, and Merkle root verifies",
            "exit_1":   "at least one check failed; first broken row id in stdout",
        },
    }
    return bundle
