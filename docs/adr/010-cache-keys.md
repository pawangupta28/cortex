# ADR-010: Semantic cache keys — canonical spec first, embeddings last

Status: accepted · 2026-11-17

## Context
Embedding similarity alone treats "revenue in 2017" and "revenue in 2018" as the same question.

## Decision
Metric questions are keyed on the compiled QuerySpec (exact match). Other questions need cosine ≥ τ **and** identical literals and recognised entities **and** token Jaccard ≥ 0.4. The role and data version are part of every key, so a hit can never cross a permission scope.

## Evidence
On paraphrase pairs from the golden set: embedding-only precision 4% (140 false hits at τ=0.97); production keys 100% precision, 65% recall, 0 false hits.
