# ADR-004: Structure-aware chunks and one index per collection

Status: accepted · 2026-11-17

## Context
Policies are 18 structured sections; reviews are 6.5k short texts. One shared LSA space was dominated by review vocabulary, and the policy question "How are sellers placed on probation?" retrieved the wrong section.

## Decision
Chunk on headings (each chunk keeps its heading path) and give each collection its own BM25 statistics and embedding space. Small collections keep rare terms (`min_df=1`); light plural folding (`carriers` → `carrier`).

## Evidence
After the change the probation and carrier questions retrieve the right section first; policy recall@3 is 100% for all three retrievers and recall@1 is 95.5% (BM25, hybrid) vs 90.9% (dense) — see evals/REPORT.md.

## Consequences
The policy corpus is too small to separate retrievers; the comparison becomes meaningful once the finance (EDGAR) domain is added.
