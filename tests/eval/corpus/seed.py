"""Sprint 5 — Seed the OWASP attack corpus into the evaluation tables.

The runner reads from eval_dataset_cases; the corpus.jsonl is the source
of truth. This loader picks up every line from corpus.jsonl and inserts
DatasetCase rows tagged with a single parent Dataset.

Usage from the repo root:

    python3 -m tests.eval.corpus.seed \\
        --tenant-id 00000000-0000-0000-0000-000000000001 \\
        --dataset-name owasp_corpus_v1

You can also call ``seed_corpus(...)`` programmatically from a fixture or
a one-shot script — it commits inline and returns the (dataset_id,
case_count) tuple so callers can immediately enqueue an eval job.

NEVER seed against a production tenant — the corpus is large and the
runner will spam /execute. Use the demo tenant ID or a dedicated one.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

from sqlalchemy import select

from sdk.common.db import get_session_factory
from services.audit.models import EvalDataset, EvalDatasetCase

CORPUS_PATH = Path(__file__).parent / "corpus.jsonl"


def _load_cases(path: Path = CORPUS_PATH) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(
            f"corpus.jsonl missing at {path} — run "
            f"`python3 -m tests.eval.corpus.generate` first."
        )
    with path.open("r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


async def seed_corpus(
    tenant_id: uuid.UUID,
    dataset_name: str = "owasp_corpus_v1",
    dataset_version: str = "1",
    description: str | None = None,
    overwrite: bool = False,
) -> tuple[uuid.UUID, int]:
    """Insert (or overwrite) a dataset + all cases. Idempotent on name+version."""
    cases = _load_cases()
    factory = get_session_factory()

    async with factory() as session:
        existing = (
            await session.execute(
                select(EvalDataset).where(
                    EvalDataset.tenant_id == tenant_id,
                    EvalDataset.name == dataset_name,
                    EvalDataset.version == dataset_version,
                )
            )
        ).scalar_one_or_none()

        if existing and not overwrite:
            return existing.id, existing.case_count

        if existing and overwrite:
            await session.execute(
                EvalDatasetCase.__table__.delete().where(
                    EvalDatasetCase.dataset_id == existing.id
                )
            )
            await session.delete(existing)
            await session.commit()

        ds_id = uuid.uuid4()
        kinds = {c["case_kind"] for c in cases}
        dataset_kind = (
            "attack" if kinds == {"attack"}
            else "benign" if kinds == {"benign"}
            else "mixed"
        )
        ds = EvalDataset(
            id=ds_id,
            tenant_id=tenant_id,
            name=dataset_name,
            kind=dataset_kind,
            version=dataset_version,
            description=description or "OWASP LLM Top-10 attack corpus (Sprint 5).",
            case_count=len(cases),
            created_by="seed.py",
        )
        session.add(ds)

        bulk: list[EvalDatasetCase] = []
        for c in cases:
            bulk.append(
                EvalDatasetCase(
                    id=uuid.uuid4(),
                    dataset_id=ds_id,
                    tenant_id=tenant_id,
                    case_kind=c["case_kind"],
                    owasp_category=c["owasp_category"],
                    base_id=c["base_id"],
                    mutation=c["mutation"],
                    payload_json={
                        "tool":    c["tool"],
                        "payload": c["payload"],
                    },
                    expected_outcome=c["expected_outcome"],
                    expected_findings=c["expected_findings"] or [],
                    notes=c.get("notes", "") or None,
                )
            )
        session.add_all(bulk)
        await session.commit()
        return ds_id, len(bulk)


def _cli() -> int:
    parser = argparse.ArgumentParser(prog="tests.eval.corpus.seed")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--dataset-name", default="owasp_corpus_v1")
    parser.add_argument("--version", default="1")
    parser.add_argument("--description", default=None)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    try:
        tenant = uuid.UUID(args.tenant_id)
    except ValueError as exc:
        print(f"invalid --tenant-id: {exc}", file=sys.stderr)
        return 2

    async def _run() -> tuple[uuid.UUID, int]:
        return await seed_corpus(
            tenant_id=tenant,
            dataset_name=args.dataset_name,
            dataset_version=args.version,
            description=args.description,
            overwrite=args.overwrite,
        )

    ds_id, count = asyncio.run(_run())
    print(f"seeded dataset {ds_id} with {count} cases")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
