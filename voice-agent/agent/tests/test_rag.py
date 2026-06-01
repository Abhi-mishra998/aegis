"""Smoke tests for the hybrid RAG retriever.

These don't validate retrieval *quality* (that depends on the corpus +
reranker training and isn't a unit-test concern). They assert the pipeline
is wired correctly: BM25 loaded, ChromaDB queryable, cross-encoder
scoring, top_n honored, no crashes on edge inputs.

Prerequisite: a built index. Run `python src/ingest.py` from the agent
directory once before invoking pytest. If the index isn't present every
test in this file is skipped (rather than failing) — so CI can ingest in
a setup step and these run after.

Run from the agent directory:
    .venv/bin/python -m pytest tests/test_rag.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

AGENT_DIR = Path(__file__).resolve().parents[1]
CHROMA = AGENT_DIR / "chroma_db"
BM25 = AGENT_DIR / "bm25_index"

# Make `from rag import ...` work when pytest is invoked from anywhere.
SRC = AGENT_DIR / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


pytestmark = pytest.mark.skipif(
    not (CHROMA.exists() and BM25.exists()),
    reason="run `python src/ingest.py` first to build the index",
)


@pytest.fixture(scope="module")
def kb():
    from rag import AegisKnowledge
    return AegisKnowledge(persist_dir=str(CHROMA), bm25_dir=str(BM25))


def test_index_is_non_empty(kb):
    assert kb.collection.count() > 0, "dense index is empty"
    assert kb._bm25 is not None, "BM25 index didn't load"
    assert kb._bm25_docs, "BM25 has no documents"


def test_search_returns_hits_for_kill_switch(kb):
    hits = kb.search("kill switch behavior", top_n=3)
    assert hits, "expected ≥1 hit for 'kill switch behavior'"
    sources = [h.source for h in hits]
    # Top hits should land on kill-switch or decision-related docs.
    assert any(
        "kill" in s.lower() or "decision" in s.lower() for s in sources
    ), f"top hits should include kill-switch/decision docs; got {sources}"
    assert hits[0].score > 0, "top-1 reranker score should be positive"


def test_search_respects_top_n(kb):
    assert len(kb.search("audit chain", top_n=1)) == 1
    assert len(kb.search("audit chain", top_n=5)) <= 5


def test_search_returns_voice_guide_for_voice_query(kb):
    """The newest doc section (voice-guide/) must be reachable post-reingest."""
    hits = kb.search("hybrid retrieval BM25 reranker voice", top_n=5)
    assert hits, "no hits for a voice-guide-specific query"
    sources = [h.source for h in hits]
    assert any(
        "voice-guide" in s or "rag" in s.lower() for s in sources
    ), f"expected voice-guide content; got {sources}"


def test_search_handles_exact_keyword(kb):
    """BM25 should give precision on exact tokens that dense might paraphrase."""
    hits = kb.search("X-Tenant-ID header", top_n=3)
    assert hits, "no hits for 'X-Tenant-ID header'"


def test_search_empty_query_does_not_crash(kb):
    """The agent shouldn't call with empty queries, but the function
    shouldn't blow up if it does."""
    # Just assert no exception. Result content is undefined for empty input.
    kb.search("", top_n=2)


def test_hit_shape(kb):
    """Every returned Hit must carry text, source, and a numeric score."""
    hits = kb.search("policy engine", top_n=2)
    assert hits
    for h in hits:
        assert isinstance(h.text, str) and h.text.strip(), "empty hit text"
        assert isinstance(h.source, str) and h.source, "missing source"
        assert isinstance(h.score, float), f"score not float: {h.score!r}"
