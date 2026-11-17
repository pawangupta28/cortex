# 7. Verification and uncertainty

**Status: Built · measured**

## Problem
A fluent answer with one wrong number is worse than no answer. Users can't see which numbers came from the data and which the model made up, or when the system is guessing.

## Verification
Every sentence's numbers are extracted (it understands `$96.22B`, `4.5 billion`, `75.0%`) and matched against the evidence pool: SQL cells, computed facts and numbers stated in cited passages. Matching allows rounding (relative error ≤ 0.6%) and ratio → percent. Dates, quarter labels, form names ("10-Q") and item numbers are not treated as quantities.

With an LLM, failing sentences get one rewrite; if they still fail, confidence drops and agent tasks go to `needs_human`.

Test: evidence 0.2999 → "30.0%" passes; "45.0%" fails.

## Confidence
These signals are combined by logistic regression, fitted on the dev split:
- the planner's confidence;
- whether the SQL ran and returned rows, and the confidence of the query spec;
- retrieval strength;
- the share of sentences verified;
- the amount of evidence;
- whether any passage was quarantined;
- whether the question is unsupported.

## Conformal abstention
Split conformal prediction on the calibration split, with nonconformity = 1 − score for correct answers:

```
q̂ = Quantile( {1 − s_i}, ⌈(n+1)(1−α)⌉ / n ),    answer iff 1 − s(x) ≤ q̂
```

Under exchangeability this keeps a correct answer with probability ≥ 1 − α (α = 0.1).

**Per-intent (Mondrian) thresholds.** The first version used one global q̂. Metric questions, which are plentiful and score high, set a bar that every hybrid "why" question missed, so all of them were refused and accuracy dropped from 98% to 91%. The eval caught it. Each intent now has its own q̂, and a group with fewer than 5 calibration points uses only hard rules (out of scope, no evidence, no data) instead of a threshold fitted on noise.

Hard abstentions don't depend on the score: an entity known to be missing ("What was Tesla's revenue?" → "Tesla is not in this dataset"), forecasts, and off-topic questions.

## Results (holdout)
| | Finance | E-commerce |
| --- | --- | --- |
| Correct answers kept (target ≥ 90%) | 92.9% | 88.2% |
| Error rate among answered vs without abstention | 0.0% vs 0.0% | 6.3% vs 5.6% |
| Out-of-scope questions declined | 5/5 | 6/6 |

Reading: on holdouts this small (about 15–20 answers), the coverage guarantee can't be confirmed or refuted, and abstention doesn't yet reduce errors. That needs a larger calibration set. This is reported as is (ADR-015).

## Next (Designed)
Self-consistency (3–5 samples at temperature > 0) as an extra signal once LLMs are on; a larger calibration set; and selective-risk control as an alternative to coverage control.
