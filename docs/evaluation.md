# Evaluation

Every design decision in Cortex cites a measured number, and every push is gated on those numbers. One command (`python -m cortex.cli --domain <d> eval`) rebuilds the golden set, runs it, fits and calibrates the confidence model, runs the red team and the retrieval and cache studies, and writes `evals/<domain>/REPORT.md`.

## Golden sets

| Domain | Size | Where the correct answers come from |
| --- | --- | --- |
| Finance | 70 questions: 38 point values, 6 rankings, 4 trends, 11 document, 6 hybrid, 5 unanswerable | **SEC `frame` labels** in the raw companyfacts JSON. They are independent of Cortex's own quarter alignment, concept ranking and Q4 derivation, so a bug there fails the test. |
| E-commerce | 102 questions: 54 values, 6 rankings, 2 trends, 22 document, 12 hybrid, 6 unanswerable | **Hand-written SQL on base tables**, not the `order_facts` view the semantic layer uses |

- **Splits** (dev 60% / calibration 20% / holdout 20%) are assigned by hashing the template, so paraphrases of one question never straddle dev and holdout.
- **Document questions** are kept only if their key fact actually appears in that company's filings, so the set adapts when filings are refreshed.
- **Unanswerable questions** (Tesla, Meta, forecasts, off-topic) test abstention.

## Grading
- **Numbers:** every gold value must appear in the answer, within rounding (it understands $B, M and %).
- **Rankings:** the right top entity, plus its value.
- **Documents:** a relevant passage must be retrieved (right entity and contains the fact), and the fact must appear in the answer.
- **Hybrid:** the answer must name a gold driver (for example "Blackwell" or "mix" for NVIDIA's gross margin).

## Statistics

| Method | Used for |
| --- | --- |
| Bootstrap 95% CI (10,000 resamples) | Every accuracy figure |
| Paired bootstrap on per-question differences | Comparing versions and retrievers (resample questions, not answers) |
| Paired permutation test, McNemar | p-values for MRR differences and pass/fail changes |
| Power analysis | The smallest change each test set can detect: ±7.9% finance, ±6.6% e-commerce (80% power) |
| Cohen's κ | Agreement between an LLM judge and human grades (implemented; judge Designed) |
| Benjamini–Hochberg | Controlling false discoveries across many metrics |
| ECE, risk–coverage | Calibration and abstention quality |

## Current results

| | Finance (real SEC data) | E-commerce (synthetic) |
| --- | --- | --- |
| Overall | **97.1%** [92.9, 100] | **97.1%** [93.1, 100] |
| Numeric (values, rankings, trends) | 100% | 100% |
| Document | 81.8% | 95.5% |
| Hybrid | 100% | 83.3% |
| Unanswerable declined | 100% | 100% |
| Latency p50 / p95 (offline) | 28 / 59 ms | 37 / 55 ms |

**Failures, reported rather than tuned away:**
- **Finance:** "Where does NVIDIA manufacture its chips?" The answer (TSMC) is in the 10-K's Manufacturing section, but the question's wording doesn't match it. Lexical retrieval can't bridge that; a real embedding model is the planned fix.
- **E-commerce:** "What if a product stops working after a month?" This was lost when the docs probe was made stricter to stop off-topic questions being answered from SEC text (ADR-019). Two hybrid paraphrases surface "late delivery" before the planted product theme.

## Honest limits
- The question templates were used while building the parser, so these are **development benchmarks and regression baselines**, not estimates of accuracy on unseen users.
- Holdouts are small (about 15–20 answered questions), so conformal coverage can't be confirmed at this size.
- All numbers above are from **offline mode**: no LLM wrote these answers. Live runs (`--sample N` for free-tier limits) write `REPORT.llm.md` next to the offline report, with rewrite rate, verified-sentence rate and cost, so the two modes can be compared directly.

## CI gate
On every push (`.github/workflows/ci.yml`): lint → 40 tests → ingest → full eval. The build fails if:
- the upper bound of overall accuracy's 95% interval drops below the baseline minus 3 points;
- the attack success rate with defences on rises;
- any row-level security leak appears.
