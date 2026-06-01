"""Walk a docs directory, heading-chunk every .md file, embed into ChromaDB,
AND build a BM25 sparse index. Both are required for the hybrid RAG pipeline.

Usage:
    python src/ingest.py [DOCS_DIR]

DOCS_DIR defaults to ../docs (repo-root /docs from agent/).
Re-running replaces both indexes, never duplicates.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from rag import AegisKnowledge

MAX_CHARS = 1200
OVERLAP = 150
HEADING_RE = re.compile(r"(?m)^(#{1,6}\s.*)$")


def chunk_markdown(text: str, max_chars: int = MAX_CHARS, overlap: int = OVERLAP) -> list[str]:
    parts = HEADING_RE.split(text)
    sections: list[str] = []
    buf = ""
    for p in parts:
        if not p:
            continue
        if HEADING_RE.match(p):
            if buf.strip():
                sections.append(buf.strip())
            buf = p + "\n"
        else:
            buf += p
    if buf.strip():
        sections.append(buf.strip())

    chunks: list[str] = []
    for sec in sections:
        if len(sec) <= max_chars:
            chunks.append(sec)
            continue
        start = 0
        while start < len(sec):
            chunks.append(sec[start : start + max_chars])
            start += max_chars - overlap
    return [c for c in chunks if c.strip()]


def collect_markdown(docs_dir: Path) -> list[Path]:
    return sorted(p for p in docs_dir.rglob("*.md") if p.is_file())


def main(docs_dir: str) -> None:
    root = Path(docs_dir).resolve()
    if not root.is_dir():
        sys.exit(f"docs dir not found: {root}")

    files = collect_markdown(root)
    if not files:
        sys.exit(f"no .md files under {root}")

    agent_dir = Path(__file__).resolve().parents[1]
    kb = AegisKnowledge(
        persist_dir=str(agent_dir / "chroma_db"),
        bm25_dir=str(agent_dir / "bm25_index"),
    )
    kb.reset()

    all_docs: list[str] = []
    all_ids: list[str] = []
    all_meta: list[dict] = []
    for f in files:
        rel = f.relative_to(root).as_posix()
        text = f.read_text(encoding="utf-8")
        for i, chunk in enumerate(chunk_markdown(text)):
            all_docs.append(chunk)
            all_ids.append(f"{rel}#{i}")
            all_meta.append({"source": rel})

    # Chroma has per-call size limits; insert in batches.
    batch = 256
    for i in range(0, len(all_docs), batch):
        kb.add(all_docs[i : i + batch], all_ids[i : i + batch], all_meta[i : i + batch])

    # Build & persist the BM25 sparse index alongside the dense one.
    kb.save_bm25(all_docs, all_meta)

    print(
        f"Ingested {len(all_docs)} chunks from {len(files)} files under {root} "
        f"(dense + BM25)."
    )


if __name__ == "__main__":
    default = Path(__file__).resolve().parents[2] / "docs"
    main(sys.argv[1] if len(sys.argv) > 1 else str(default))
