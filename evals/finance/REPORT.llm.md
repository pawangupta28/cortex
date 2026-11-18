# Cortex evaluation report: finance

Generated 2026-11-18 17:06:37 · data: sec-edgar · mode: online · 20 questions · runtime 302.4 s

> **Read this first.** Real data (sec-edgar). Gold answers come from an independent source (SEC frame labels / hand-written SQL on base tables), but the question templates were used while building the parser and the holdout split is small, so treat this as a regression baseline and test with your own questions before quoting a number.

## Accuracy (mean, 95% bootstrap CI)

| Type | Accuracy | CI |
| --- | --- | --- |
| docs | 75.0% | [25.0%, 100.0%] |
| hybrid | 100.0% | [100.0%, 100.0%] |
| metric_rank | 100.0% | [100.0%, 100.0%] |
| metric_trend | 100.0% | [100.0%, 100.0%] |
| metric_value | 100.0% | [100.0%, 100.0%] |
| unanswerable | 100.0% | [100.0%, 100.0%] |
| **overall** | **95.0%** | [85.0%, 100.0%] |

Smallest change this test set can reliably detect: ±19.3% (80% power).

Latency p50 10056.1 ms, p95 15274.4 ms.

Models: openai/gpt-oss-120b · verifier-forced rewrites: 10% of questions · verified sentences: 96% · shadow cost $0.0097 · stratified sample of 20

## Live-model accuracy

20 of 20 questions (100%) were answered entirely by a live model; none fell back to the offline path.

| Type | Accuracy (fully live) | CI |
| --- | --- | --- |
| docs | 75.0% | [25.0%, 100.0%] |
| hybrid | 100.0% | [100.0%, 100.0%] |
| metric_rank | 100.0% | [100.0%, 100.0%] |
| metric_trend | 100.0% | [100.0%, 100.0%] |
| metric_value | 100.0% | [100.0%, 100.0%] |
| unanswerable | 100.0% | [100.0%, 100.0%] |
| **overall** | **95.0%** | [85.0%, 100.0%] |

Calls by provider: {'groq': 81} · tokens: {'groq': 40568}

## Retrieval study (reference documents)

| Retriever | Recall@3 | CI | Recall@1 | MRR |
| --- | --- | --- | --- | --- |
| bm25 | 100.0% | [100.0%, 100.0%] | 100.0% | 1.000 |
| dense | 100.0% | [100.0%, 100.0%] | 75.0% | 0.833 |
| hybrid | 100.0% | [100.0%, 100.0%] | 100.0% | 1.000 |

Hybrid minus BM25 recall: +0.0% (CI [0.0, 0.0]), MRR permutation p = 1.000.

## Confidence and conformal abstention (holdout)

- alpha = 0.1; global q_hat = 0.0068 (from 3 correct calibration answers)
- per-intent q_hat (Mondrian conformal): metric 0.0072 (n=7, dev+calib); docs 1.0 (n=4, insufficient data: score abstention off); hybrid 1.0 (n=2, insufficient data: score abstention off)
- correct answers kept: 1.0 (target ≥ 90%)
- error rate among answered: 0.0 vs 0.0 without abstention
- share of holdout answered: 1.0
- ECE: default weights 0.0055 → fitted 0.0055

## Red team

| Setting | Attack success rate |
| --- | --- |
| defences off | 0% |
| defences on | 0% |

SQL guard blocked 12/12 malicious statements. Row-level security (semis_analyst): no rows outside the role scope.

## Model router (simulation with known ground truth)

Bandit router vs always-frontier: cost per call 0.002454 vs 0.006 (**−59.1%**), quality 0.8816 vs 0.9166 (−3.5 pts) under a 0.0025 per-call budget. Arm share: {'frontier': 0.299, 'mid': 0.398, 'small': 0.303}.

## Semantic cache threshold sweep (paraphrase pairs)

| Threshold | Embedding only: precision / false hits | + signature (spec or literals): precision / false hits | + signature + Jaccard ≥ 0.4 (production): precision / recall / false hits |
| --- | --- | --- | --- |
| 0.5 | 0.1471 / 29 | 1.0 / 0 | 1.0 / 0.2 / 0 |
| 0.7 | 0.2 / 12 | 1.0 / 0 | 1.0 / 0.2 / 0 |
| 0.8 | 0.2 / 8 | 1.0 / 0 | 1.0 / 0.2 / 0 |
| 0.9 | 0.3333 / 2 | 1.0 / 0 | 1.0 / 0.2 / 0 |
| 0.95 | 0.0 / 1 | 1.0 / 0 | 1.0 / 0.2 / 0 |
| 0.97 | 1.0 / 0 | 1.0 / 0 | 1.0 / 0.2 / 0 |

Embeddings alone confuse 'revenue in 2017' with 'revenue in 2018'. The cache therefore also requires matching literals (numbers, state codes), and metric questions are keyed on the compiled query spec, so they never depend on embedding similarity.

## Failures (first 25)

- `doc_AAPL_tariffs#1`: I can't answer this reliably (outside the data this system covers). This dataset covers Apple, Microsoft, NVIDIA, Alphabet and Amazon: their reported financials
