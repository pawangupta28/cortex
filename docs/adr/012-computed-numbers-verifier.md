# ADR-012: Numbers are computed in code and verified before they ship

Status: accepted · 2026-11-17

## Decision
The analyst derives every figure (top/bottom, spread, change, distinctive terms) in Python from SQL results. The writer may only restate evidence numbers; the verifier checks each sentence's numbers against SQL cells, computed facts and cited text (allowing rounding and ratio→percent). With an LLM, failing sentences get one rewrite; then confidence drops.

## Evidence
Unit test: "45.0%" against evidence 0.2999 fails; "30.0%" passes. All metric answers in the eval are fully verified.
