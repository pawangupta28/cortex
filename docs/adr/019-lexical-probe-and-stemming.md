# ADR-019: Lexical docs probe needs 75% term coverage; heavier stemming rejected

Status: accepted · 2026-11-17

## Context
Questions the cue lists miss are rescued by a BM25 probe into the reference corpus. On 1,375 SEC passages, "What is the capital of France?" scored above the old threshold because "capital" is common in filings.

## Decision
The probe now also requires at least 75% of the question's content terms to appear in the top passage. A suffix stemmer (manufacturing → manufactur) was tried and measured.

## Evidence
- Probe: finance unanswerable questions went from 3/5 to 5/5 declined; e-commerce lost one paraphrase ("What if a product stops working after a month?"), from 98.0% to 97.1%.
- Stemming: worse on both golden sets (e-commerce docs 100% → 90.9%, finance docs 81.8% → 72.7%), so it is off (`FULL_STEM = False`).

## Consequences
Remaining docs misses are vocabulary mismatches; the planned fix is a real embedding model, not more lexical rules.
