# ADR-016: Layered defence against direct and indirect prompt injection

Status: accepted · 2026-11-17

## Decision
(1) Score user input and every retrieved chunk with a noisy-OR pattern detector; block inputs and quarantine chunks above 0.7. (2) Spotlight remaining untrusted text in `<untrusted>` tags with datamarking. (3) The model that reads untrusted text has no write tools; writes go through the policy engine and human approval.

## Evidence
A planted instruction in `supplier_bulletin.md` succeeds in 3 of 3 indirect cases with defences off; attack success rate across 8 cases falls from 38% to 0% with defences on. The SQL guard blocks 12/12 malicious statements.

## Consequences
Pattern detectors miss novel phrasings; the next step is a small trained classifier and measuring its false-positive rate on normal questions.
