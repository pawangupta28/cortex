"""Semantic cache: reuse an answer when a new question means the same thing.

The key is (role scope, data version) + the question embedding. Scope is part of the key
because a cache hit across permission scopes would be a data leak (OWASP LLM02/LLM08).
A hit needs three things: cosine similarity >= threshold, identical salient literals
(numbers, state codes), and token Jaccard overlap >= min_jaccard. The eval's cache study
shows why: with an offline LSA embedder, cosine alone has very poor precision on short
questions. A wrong cached answer costs more than a miss, so precision is the target.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np


@dataclass
class _Entry:
    vec: np.ndarray
    value: Any
    question: str
    signature: str = ""
    tokens: frozenset = frozenset()
    created: float = field(default_factory=time.time)


class SemanticCache:
    def __init__(self, embed: Callable[[list[str]], np.ndarray], threshold: float = 0.97,
                 ttl_s: float = 3600, max_entries: int = 5000,
                 signature: Callable[[str], str] | None = None, min_jaccard: float = 0.4):
        self.embed = embed
        self.min_jaccard = min_jaccard
        # Embeddings blur exact values ("revenue in 2017" ~ "revenue in 2018"), so entries
        # must also share a signature of the salient literals: numbers, years, codes.
        self.signature = signature or (lambda q: "|".join(sorted(re.findall(r"\b\d+\b|\b[A-Z]{2}\b", q))))
        self.threshold = threshold
        self.ttl = ttl_s
        self.max = max_entries
        self.store: dict[str, list[_Entry]] = {}
        self.hits = self.misses = 0

    @staticmethod
    def scope(role: str, data_version: str) -> str:
        return f"{role}|{data_version}"

    def get(self, question: str, scope: str, signature: str | None = None) -> tuple[Any, float] | None:
        self.store[scope] = [e for e in self.store.get(scope, []) if time.time() - e.created < self.ttl]
        sig = signature if signature is not None else self.signature(question)
        entries = [e for e in self.store[scope] if e.signature == sig]
        if signature is not None and entries and not signature.startswith("lit:"):
            # a canonical form (the compiled query spec): exact match is a hit
            self.hits += 1
            return entries[-1].value, 1.0
        if not entries:
            self.misses += 1
            return None
        q = self.embed([question])[0]
        toks = self.tokens(question)
        sims = np.array([e.vec @ q for e in entries])
        i = int(np.argmax(sims))
        if sims[i] >= self.threshold and self.jaccard(toks, entries[i].tokens) >= self.min_jaccard:
            self.hits += 1
            return entries[i].value, float(sims[i])
        self.misses += 1
        return None

    def put(self, question: str, scope: str, value: Any, signature: str | None = None) -> None:
        entries = self.store.setdefault(scope, [])
        sig = signature if signature is not None else self.signature(question)
        entries.append(_Entry(self.embed([question])[0], value, question, sig, self.tokens(question)))
        if len(entries) > self.max:
            entries.pop(0)

    @staticmethod
    def tokens(q: str) -> frozenset:
        from cortex.retrieval.index import tokenize
        return frozenset(tokenize(q))

    @staticmethod
    def jaccard(a: frozenset, b: frozenset) -> float:
        return len(a & b) / len(a | b) if a | b else 1.0

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {"hits": self.hits, "misses": self.misses, "hit_rate": round(self.hits / total, 3) if total else 0.0}
