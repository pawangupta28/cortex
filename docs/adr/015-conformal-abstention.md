# ADR-015: Per-intent conformal abstention instead of a hand-picked threshold

Status: accepted · 2026-11-17

## Decision
A logistic model combines confidence signals (fitted on the dev split). Split conformal prediction sets a threshold q̂ so that correct answers are kept with probability ≥ 1 − α (α = 0.1). Thresholds are per intent (Mondrian conformal). A group with fewer than 5 calibration points gets no score-based abstention; only the hard rules apply (out of scope, no evidence, no data).

## Why per intent
With one global q̂, plentiful high-scoring metric questions set a bar that every hybrid "why" question missed, so the system refused all of them. The eval caught it: accuracy fell from 98% to 91%.

## Evidence (latest REPORT.md)
On the holdout (~18 answered questions), 88% of correct answers were kept against a 90% target, and abstention did not lower the error rate. At this sample size the guarantee cannot be shown either way; it needs a larger calibration set (the finance domain adds ~120 questions).
