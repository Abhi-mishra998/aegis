"""Hybrid RAG: BM25 (sparse) + dense vectors + cross-encoder reranker.

Pipeline:
  user query
    │
    ├──► BM25 top-k_bm25  ──┐
    │                       │
    └──► dense top-k_dense ─┴──► merge (dedup) ──► cross-encoder rerank ──► top-N

All components run on CPU. The cross-encoder model is ~22 MB and scores
20 candidates in well under 100 ms on t3.medium.
"""

from __future__ import annotations

import pickle
import re
from dataclasses import dataclass
from pathlib import Path

from chromadb import PersistentClient
from chromadb.utils import embedding_functions
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder

EMBED_MODEL = "all-MiniLM-L6-v2"
RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
COLLECTION = "aegis"

_TOKEN_RE = re.compile(r"\w+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


@dataclass
class Hit:
    text: str
    source: str
    score: float


class AegisKnowledge:
    """Hybrid retrieval over an Aegis docs corpus."""

    def __init__(
        self,
        persist_dir: str = "./chroma_db",
        bm25_dir: str = "./bm25_index",
    ) -> None:
        self._persist_dir = persist_dir
        self._bm25_dir = Path(bm25_dir)

        self._client = PersistentClient(path=persist_dir)
        self._ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=EMBED_MODEL
        )
        self.collection = self._client.get_or_create_collection(
            name=COLLECTION,
            embedding_function=self._ef,
            metadata={"hnsw:space": "cosine"},
        )

        # Reranker is heavy-ish to load (~22 MB + tokenizer); load once per process.
        self._reranker = CrossEncoder(RERANKER_MODEL)

        self._bm25, self._bm25_docs, self._bm25_meta = self._load_bm25()

    # -- index management ---------------------------------------------------

    def reset(self) -> None:
        """Drop and recreate the dense collection. BM25 is overwritten by save_bm25()."""
        try:
            self._client.delete_collection(COLLECTION)
        except Exception:
            pass
        self.collection = self._client.get_or_create_collection(
            name=COLLECTION,
            embedding_function=self._ef,
            metadata={"hnsw:space": "cosine"},
        )

    def add(self, docs: list[str], ids: list[str], metadatas: list[dict]) -> None:
        self.collection.add(documents=docs, ids=ids, metadatas=metadatas)

    def save_bm25(self, docs: list[str], metas: list[dict]) -> None:
        """Build and pickle the BM25 index. Called once per ingest run."""
        self._bm25_dir.mkdir(parents=True, exist_ok=True)
        bm25 = BM25Okapi([_tokenize(d) for d in docs])
        with (self._bm25_dir / "index.pkl").open("wb") as f:
            pickle.dump({"bm25": bm25, "docs": docs, "meta": metas}, f)
        self._bm25, self._bm25_docs, self._bm25_meta = bm25, docs, metas

    def _load_bm25(self):
        idx_path = self._bm25_dir / "index.pkl"
        if not idx_path.exists():
            return None, [], []
        with idx_path.open("rb") as f:
            data = pickle.load(f)
        return data["bm25"], data["docs"], data["meta"]

    # -- retrieval ---------------------------------------------------------

    def search(
        self,
        query: str,
        k_dense: int = 10,
        k_bm25: int = 10,
        top_n: int = 2,
    ) -> list[Hit]:
        """Hybrid retrieve + rerank. Returns the top_n highest-scoring chunks."""
        candidates: dict[str, dict] = {}  # text -> meta

        # 1. Dense retrieval (semantic)
        if self.collection.count() > 0:
            res = self.collection.query(query_texts=[query], n_results=k_dense)
            docs = res.get("documents", [[]])[0]
            metas = res.get("metadatas", [[]])[0]
            for d, m in zip(docs, metas):
                candidates[d] = m or {}

        # 2. BM25 retrieval (lexical)
        if self._bm25 is not None and self._bm25_docs:
            scores = self._bm25.get_scores(_tokenize(query))
            top_idx = sorted(
                range(len(scores)), key=lambda i: scores[i], reverse=True
            )[:k_bm25]
            for i in top_idx:
                candidates.setdefault(self._bm25_docs[i], self._bm25_meta[i] or {})

        if not candidates:
            return []

        # 3. Cross-encoder rerank
        items = list(candidates.items())  # [(text, meta), ...]
        pairs = [(query, text) for text, _ in items]
        rerank_scores = self._reranker.predict(pairs)

        ranked = sorted(
            zip(items, rerank_scores), key=lambda x: x[1], reverse=True
        )[:top_n]

        return [
            Hit(
                text=text,
                source=(meta or {}).get("source", "unknown"),
                score=float(score),
            )
            for (text, meta), score in ranked
        ]
