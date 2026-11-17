# Cortex evaluation report: ecommerce

Generated 2026-11-17 16:33:14 · data: synthetic · mode: offline-deterministic · 102 questions · runtime 5.6 s

> **Read this first.** These are development numbers on synthetic data with planted structure. The question templates were used while building the parser, and the holdout split is small. Treat them as a regression baseline, not real-world accuracy.

## Accuracy (mean, 95% bootstrap CI)

| Type | Accuracy | CI |
| --- | --- | --- |
| docs | 95.5% | [86.4%, 100.0%] |
| hybrid | 83.3% | [58.3%, 100.0%] |
| metric_rank | 100.0% | [100.0%, 100.0%] |
| metric_trend | 100.0% | [100.0%, 100.0%] |
| metric_value | 100.0% | [100.0%, 100.0%] |
| unanswerable | 100.0% | [100.0%, 100.0%] |
| **overall** | **97.1%** | [93.1%, 100.0%] |

Smallest change this test set can reliably detect: ±6.6% (80% power).

Latency p50 36.9 ms, p95 55.1 ms.

## Retrieval study (reference documents)

| Retriever | Recall@3 | CI | Recall@1 | MRR |
| --- | --- | --- | --- | --- |
| bm25 | 100.0% | [100.0%, 100.0%] | 95.5% | 0.977 |
| dense | 100.0% | [100.0%, 100.0%] | 90.9% | 0.955 |
| hybrid | 100.0% | [100.0%, 100.0%] | 95.5% | 0.977 |

Hybrid minus BM25 recall: +0.0% (CI [0.0, 0.0]), MRR permutation p = 1.000.

## Confidence and conformal abstention (holdout)

- alpha = 0.1; global q_hat = 0.035 (from 20 correct calibration answers)
- per-intent q_hat (Mondrian conformal): metric 0.0086 (n=17, calib); docs 0.0374 (n=23, dev+calib); hybrid 1.0 (n=1, insufficient data: score abstention off)
- correct answers kept: 0.882 (target ≥ 90%)
- error rate among answered: 0.0625 vs 0.0556 without abstention
- share of holdout answered: 0.889
- ECE: default weights 0.0328 → fitted 0.0368

## Red team

| Setting | Attack success rate |
| --- | --- |
| defences off | 38% |
| defences on | 0% |

SQL guard blocked 12/12 malicious statements. Row-level security (regional_manager_sp): no rows outside the role scope.

## Model router (simulation with known ground truth)

Bandit router vs always-frontier: cost per call 0.002478 vs 0.006 (**−58.7%**), quality 0.8817 vs 0.9166 (−3.49 pts) under a 0.0025 per-call budget. Arm share: {'frontier': 0.305, 'mid': 0.393, 'small': 0.302}.

## Semantic cache threshold sweep (paraphrase pairs)

| Threshold | Embedding only: precision / false hits | + signature (spec or literals): precision / false hits | + signature + Jaccard ≥ 0.4 (production): precision / recall / false hits |
| --- | --- | --- | --- |
| 0.5 | 0.0431 / 311 | 0.6667 / 6 | 1.0 / 0.6471 / 0 |
| 0.7 | 0.0347 / 250 | 0.7778 / 2 | 1.0 / 0.6471 / 0 |
| 0.8 | 0.0367 / 210 | 0.875 / 1 | 1.0 / 0.6471 / 0 |
| 0.9 | 0.0435 / 154 | 0.875 / 1 | 1.0 / 0.6471 / 0 |
| 0.95 | 0.0437 / 153 | 0.875 / 1 | 1.0 / 0.6471 / 0 |
| 0.97 | 0.0411 / 140 | 1.0 / 0 | 1.0 / 0.6471 / 0 |

Embeddings alone confuse 'revenue in 2017' with 'revenue in 2018'. The cache therefore also requires matching literals (numbers, state codes), and metric questions are keyed on the compiled query spec, so they never depend on embedding similarity.

## Failures (first 25)

- `doc_warranty#1`: I can't answer this reliably (outside the data this system covers). Try asking about orders, revenue, deliveries, reviews or store policies.
- `why_watches_gifts#1`: In the 105 matching reviews, the most distinctive themes are 'late', 'waited', 'past', 'promised' [E1]. Sellers in furniture_decor and office_furniture must use
- `why_bed_bath_table#1`: In the 115 matching reviews, the most distinctive themes are 'wrong', 'described', 'waited', 'past' [E1]. Sellers in furniture_decor and office_furniture must u
