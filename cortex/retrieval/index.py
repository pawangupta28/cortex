"""Layer 2 retrieval: BM25 + dense vectors, fused with Reciprocal Rank Fusion, diversified with MMR.

The dense embedder defaults to LSA (TF-IDF -> truncated SVD, L2-normalised), which runs
offline with no model download. Set CORTEX_EMBEDDER=st to use a sentence-transformers
model instead; the index interface does not change.
"""
from __future__ import annotations

import math
import os
import pickle
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from cortex.config import settings
from cortex.core.models import Chunk, Evidence

TOKEN = re.compile(r"[a-z0-9_]+")
STOP = set("the a an and or of to in on for is are was were be by with at as it this that from not no does has than".split())


def _stem(t: str) -> str:
    """Light plural folding only (carriers -> carrier, deliveries -> delivery); heavier
    stemmers hurt precision on short policy text and make extracted themes unreadable."""
    if len(t) > 4 and t.endswith("ies"):
        return t[:-3] + "y"
    if len(t) > 3 and t.endswith("s") and not t.endswith(("ss", "us", "is")):
        return t[:-1]
    return t


def tokenize(text: str) -> list[str]:
    return [_stem(t) for t in TOKEN.findall(text.lower()) if t not in STOP]


# ---------------------------------------------------------------- BM25
class BM25:
    """Okapi BM25. score(q,d) = sum_t idf(t) * tf(t,d)(k1+1) / (tf(t,d) + k1(1 - b + b|d|/avgdl))."""

    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tfs = [Counter(d) for d in docs]
        self.lens = np.array([len(d) for d in docs], dtype=float)
        self.avgdl = float(self.lens.mean()) if len(docs) else 0.0
        df = Counter(t for d in docs for t in set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.postings: dict[str, list[int]] = {}
        for i, tf in enumerate(self.tfs):
            for t in tf:
                self.postings.setdefault(t, []).append(i)

    def scores(self, query: list[str]) -> np.ndarray:
        s = np.zeros(len(self.tfs))
        for t in set(query):
            if t not in self.idf:
                continue
            idf = self.idf[t]
            for i in self.postings[t]:
                tf = self.tfs[i][t]
                s[i] += idf * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * self.lens[i] / self.avgdl))
        return s


# ---------------------------------------------------------------- embedders
class LSAEmbedder:
    def __init__(self, dim: int = 192):
        self.dim = dim
        self.vec = None
        self.svd = None

    def fit(self, texts: list[str]) -> "LSAEmbedder":
        from sklearn.decomposition import TruncatedSVD
        from sklearn.feature_extraction.text import TfidfVectorizer
        # small collections keep rare terms (min_df=1): in 20 policy chunks, a word like
        # "probation" appears once and is exactly what a user will search for
        self.vec = TfidfVectorizer(tokenizer=tokenize, token_pattern=None, ngram_range=(1, 2),
                                   min_df=1 if len(texts) < 500 else 2, sublinear_tf=True)
        x = self.vec.fit_transform(texts)
        self.svd = TruncatedSVD(n_components=min(self.dim, x.shape[1] - 1), random_state=0).fit(x)
        return self

    def embed(self, texts: list[str]) -> np.ndarray:
        z = self.svd.transform(self.vec.transform(texts))
        return z / np.maximum(np.linalg.norm(z, axis=1, keepdims=True), 1e-9)


class STEmbedder:  # pragma: no cover - optional
    def __init__(self, name: str = "BAAI/bge-small-en-v1.5"):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(name)

    def fit(self, texts: list[str]) -> "STEmbedder":
        return self

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self.model.encode(texts, normalize_embeddings=True))


def make_embedder():
    return STEmbedder() if os.environ.get("CORTEX_EMBEDDER") == "st" else LSAEmbedder()


# ---------------------------------------------------------------- fusion
def rrf(rankings: list[list[int]], k: int = 60, weights=None) -> dict[int, float]:
    """Weighted Reciprocal Rank Fusion: RRF(d) = sum_r w_r / (k + rank_r(d)). Rank-based, so it
    needs no score calibration between BM25 (unbounded) and cosine ([-1, 1])."""
    weights = weights or [1.0] * len(rankings)
    out: dict[int, float] = {}
    for w, ranking in zip(weights, rankings):
        for rank, idx in enumerate(ranking, start=1):
            out[idx] = out.get(idx, 0.0) + w / (k + rank)
    return out


def mmr(query_vec: np.ndarray, cand_vecs: np.ndarray, relevance: np.ndarray, k: int, lam: float = 0.7) -> list[int]:
    """Maximal Marginal Relevance: argmax lam*rel(d) - (1-lam)*max_{s in S} sim(d, s)."""
    selected: list[int] = []
    remaining = list(range(len(cand_vecs)))
    rel = (relevance - relevance.min()) / (np.ptp(relevance) + 1e-9)
    sim = cand_vecs @ cand_vecs.T
    while remaining and len(selected) < k:
        if not selected:
            best = max(remaining, key=lambda i: rel[i])
        else:
            best = max(remaining, key=lambda i: lam * rel[i] - (1 - lam) * max(sim[i, j] for j in selected))
        selected.append(best)
        remaining.remove(best)
    return selected


@dataclass
class SearchResult:
    chunk: Chunk
    score: float
    ranks: dict[str, int] = field(default_factory=dict)


class HybridIndex:
    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        texts = [f"{c.section_path} {c.text}" for c in chunks]
        self.bm25 = BM25([tokenize(t) for t in texts])
        self.embedder = make_embedder().fit(texts)
        self.vectors = self.embedder.embed(texts).astype(np.float32)

    # -- persistence
    def save(self, path=None) -> None:
        path = path or settings.artifacts_dir / "index.pkl"
        with open(path, "wb") as f:
            pickle.dump(self, f)

    @staticmethod
    def load(path=None) -> "HybridIndex":
        path = path or settings.artifacts_dir / "index.pkl"
        with open(path, "rb") as f:
            return pickle.load(f)

    # -- search
    def _mask(self, where: dict[str, Any] | Callable[[dict], bool] | None) -> np.ndarray:
        if where is None:
            return np.ones(len(self.chunks), dtype=bool)
        if callable(where):
            return np.array([bool(where(c.metadata)) for c in self.chunks])
        return np.array([all(c.metadata.get(k) == v for k, v in where.items()) for c in self.chunks])

    def search(self, query: str, k: int = 8, mode: str = "hybrid", where=None, diversify: bool = True,
               pool: int = 50, weights: tuple[float, float] = (1.0, 1.0)) -> list[SearchResult]:
        mask = self._mask(where)
        if not mask.any():
            return []
        idx = np.flatnonzero(mask)
        bm = self.bm25.scores(tokenize(query))[idx]
        qv = self.embedder.embed([query])[0]
        dn = self.vectors[idx] @ qv
        bm_rank = list(np.argsort(-bm)[:pool])
        dn_rank = list(np.argsort(-dn)[:pool])
        if mode == "bm25":
            order, scores = bm_rank, {i: float(bm[i]) for i in bm_rank}
        elif mode == "dense":
            order, scores = dn_rank, {i: float(dn[i]) for i in dn_rank}
        else:
            fused = rrf([[i for i in bm_rank if bm[i] > 0], dn_rank], weights=weights)
            order = sorted(fused, key=lambda i: -fused[i])[:pool]
            scores = fused
        if diversify and len(order) > k:
            sel = mmr(qv, self.vectors[idx[order]], np.array([scores[i] for i in order]), k)
            order = [order[i] for i in sel]
        out = []
        for i in order[:k]:
            out.append(SearchResult(self.chunks[idx[i]], float(scores[i]),
                                    {"bm25": bm_rank.index(i) + 1 if i in bm_rank else -1,
                                     "dense": dn_rank.index(i) + 1 if i in dn_rank else -1}))
        return out


def to_evidence(results: list[SearchResult]) -> list[Evidence]:
    return [Evidence(id=f"doc:{r.chunk.id}", kind="chunk", content=r.chunk.text, score=r.score,
                     source=f"{r.chunk.doc_id}#{r.chunk.section_path}", data={"metadata": r.chunk.metadata})
            for r in results]


class Corpus:
    """One hybrid index per collection. Policies (a few dozen chunks) and reviews (thousands of
    short texts) have very different vocabularies; a shared LSA space would be dominated by
    reviews, so each collection gets its own BM25 statistics and embedding space (ADR-004)."""

    def __init__(self, chunks: list[Chunk]):
        groups: dict[str, list[Chunk]] = {}
        for c in chunks:
            groups.setdefault(c.metadata.get("type", "other"), []).append(c)
        self.collections = {name: HybridIndex(cs) for name, cs in groups.items()}
        self.query_embedder = LSAEmbedder().fit([c.text for c in chunks])   # used by the semantic cache

    def __getitem__(self, name: str) -> HybridIndex:
        return self.collections[name]

    @property
    def chunks(self) -> list[Chunk]:
        return [c for idx in self.collections.values() for c in idx.chunks]

    def save(self, path=None) -> None:
        path = path or settings.artifacts_dir / "index.pkl"
        with open(path, "wb") as f:
            pickle.dump(self, f)

    @staticmethod
    def load(path=None) -> "Corpus":
        path = path or settings.artifacts_dir / "index.pkl"
        with open(path, "rb") as f:
            return pickle.load(f)


def build_index() -> "Corpus":
    import json

    from cortex.ingest.loader import connect
    con = connect()
    rows = con.execute("SELECT id, doc_id, text, section_path, metadata FROM chunks").fetchall()
    con.close()
    chunks = [Chunk(id=r[0], doc_id=r[1], text=r[2], section_path=r[3], metadata=json.loads(r[4])) for r in rows]
    corpus = Corpus(chunks)
    corpus.save()
    return corpus
