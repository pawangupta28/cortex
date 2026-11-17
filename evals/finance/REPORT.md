# Cortex evaluation report: finance

Generated 2026-11-17 16:33:07 · data: sec-edgar · mode: offline-deterministic · 70 questions · runtime 5.8 s

> **Read this first.** Real data (sec-edgar). Gold answers come from an independent source (SEC frame labels / hand-written SQL on base tables), but the question templates were used while building the parser and the holdout split is small, so treat this as a regression baseline and test with your own questions before quoting a number.

## Accuracy (mean, 95% bootstrap CI)

| Type | Accuracy | CI |
| --- | --- | --- |
| docs | 81.8% | [54.5%, 100.0%] |
| hybrid | 100.0% | [100.0%, 100.0%] |
| metric_rank | 100.0% | [100.0%, 100.0%] |
| metric_trend | 100.0% | [100.0%, 100.0%] |
| metric_value | 100.0% | [100.0%, 100.0%] |
| unanswerable | 100.0% | [100.0%, 100.0%] |
| **overall** | **97.1%** | [92.9%, 100.0%] |

Smallest change this test set can reliably detect: ±7.9% (80% power).

Latency p50 28.4 ms, p95 58.5 ms.

## Retrieval study (reference documents)

| Retriever | Recall@3 | CI | Recall@1 | MRR |
| --- | --- | --- | --- | --- |
| bm25 | 81.8% | [54.5%, 100.0%] | 63.6% | 0.727 |
| dense | 63.6% | [36.4%, 90.9%] | 54.5% | 0.576 |
| hybrid | 81.8% | [54.5%, 100.0%] | 63.6% | 0.727 |

Hybrid minus BM25 recall: +0.0% (CI [0.0, 0.0]), MRR permutation p = 1.000.

## Confidence and conformal abstention (holdout)

- alpha = 0.1; global q_hat = 0.1925 (from 19 correct calibration answers)
- per-intent q_hat (Mondrian conformal): metric 0.0245 (n=14, calib); docs 0.1936 (n=9, dev+calib); hybrid 0.0409 (n=5, dev+calib)
- correct answers kept: 0.929 (target ≥ 90%)
- error rate among answered: 0.0 vs 0.0 without abstention
- share of holdout answered: 0.929
- ECE: default weights 0.0083 → fitted 0.0247

## Red team

| Setting | Attack success rate |
| --- | --- |
| defences off | 38% |
| defences on | 0% |

SQL guard blocked 12/12 malicious statements. Row-level security (semis_analyst): no rows outside the role scope.

## Model router (simulation with known ground truth)

Bandit router vs always-frontier: cost per call 0.002478 vs 0.006 (**−58.7%**), quality 0.8817 vs 0.9166 (−3.49 pts) under a 0.0025 per-call budget. Arm share: {'frontier': 0.305, 'mid': 0.393, 'small': 0.302}.

## Semantic cache threshold sweep (paraphrase pairs)

| Threshold | Embedding only: precision / false hits | + signature (spec or literals): precision / false hits | + signature + Jaccard ≥ 0.4 (production): precision / recall / false hits |
| --- | --- | --- | --- |
| 0.5 | 0.0588 / 400 | 1.0 / 0 | 1.0 / 0.5 / 0 |
| 0.7 | 0.104 / 155 | 1.0 / 0 | 1.0 / 0.4615 / 0 |
| 0.8 | 0.1852 / 66 | 1.0 / 0 | 1.0 / 0.4615 / 0 |
| 0.9 | 0.3846 / 16 | 1.0 / 0 | 1.0 / 0.4615 / 0 |
| 0.95 | 0.3333 / 2 | 1.0 / 0 | 1.0 / 0.4615 / 0 |
| 0.97 | 1.0 / 0 | 1.0 / 0 | 1.0 / 0.4615 / 0 |

Embeddings alone confuse 'revenue in 2017' with 'revenue in 2018'. The cache therefore also requires matching literals (numbers, state codes), and metric questions are keyed on the compiled query spec, so they never depend on embedding similarity.

## Failures (first 25)

- `doc_NVDA_foundry#0`: Introduced with the Blackwell architecture, our data-center-scale offerings feature extreme co-design where the infrastructure's chips, networking, systems, sof
- `doc_NVDA_foundry#1`: NVIDIA pioneered accelerated computing to help solve the most challenging computational problems. NVIDIA is now a data center scale AI infrastructure company re
