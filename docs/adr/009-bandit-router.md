# ADR-009: Contextual-bandit model routing with a Lagrangian budget

Status: accepted · 2026-11-17

## Decision
Linear Thompson sampling over providers, context = task type + log prompt size, reward = quality − λ·cost − μ·latency, with λ updated by dual ascent on the per-call budget. Quality comes from the verifier pass rate (and later the LLM judge).

## Evidence
In a simulation with known ground truth (three arms, three task types): cost per call 0.00248 vs 0.006 for always-frontier (−58.7%), quality −3.5 points, spend converging to the 0.0025 budget.

## Consequences
Real-traffic numbers need live providers; until then the simulation is labelled as a simulation everywhere.
