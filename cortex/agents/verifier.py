"""Verifier: every number in the answer must trace to evidence (a SQL cell, a computed
fact, or a number written in a cited document). Unmatched numbers fail the claim.

Matching accepts the representations a writer legitimately uses: the raw value, the value
rounded, and a ratio shown as a percentage (0.2999 -> 30.0%).
"""
from __future__ import annotations

import re

from cortex.core.models import Claim, Evidence

NUM = re.compile(r"(?<![\w.])[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.])[-+]?\d+(?:\.\d+)?")
SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\[])|\n+")


def _numbers(text: str) -> list[float]:
    text = re.sub(r"\[E\d+\]", " ", text)            # citation markers are not claims
    text = re.sub(r"\b\d{4}-\d{2}\b|\b\d{4}-Q\d\b", " ", text)  # period labels
    out = []
    for m in NUM.findall(text):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def evidence_numbers(evidence: list[Evidence]) -> list[float]:
    vals: list[float] = []
    for e in evidence:
        for v in e.data.values():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                vals.append(float(v))
        for row in e.data.get("rows", []):
            for v in row:
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    vals.append(float(v))
        vals += _numbers(e.content)
    return vals


def _matches(x: float, pool: list[float]) -> bool:
    for v in pool:
        for cand in (v, v * 100):
            if cand == 0 and x == 0:
                return True
            if cand != 0 and abs(x - cand) / abs(cand) <= 0.006:      # rounding tolerance
                return True
            if abs(x - cand) < 0.051 and abs(cand) < 10:              # 1-decimal rounding of small values
                return True
    return False


def verify(answer_text: str, evidence: list[Evidence], ignore: set[float] | None = None) -> list[Claim]:
    ignore = ignore or set()
    pool = evidence_numbers(evidence)
    claims = []
    for sent in [s.strip() for s in SENT.split(answer_text) if s.strip()]:
        nums = [n for n in _numbers(sent) if n not in ignore and not (1990 <= n <= 2100 and n == int(n))]
        cited = re.findall(r"\[E(\d+)\]", sent)
        ok = all(_matches(n, pool) for n in nums)
        claims.append(Claim(text=sent, numbers=nums, evidence_ids=[f"E{c}" for c in cited], verified=ok))
    return claims
