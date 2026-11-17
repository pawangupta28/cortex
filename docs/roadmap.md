# Roadmap

Each item is specified well enough to build in about a day, with the measurement that will decide whether it worked. All results will be reported next to the current offline numbers rather than replacing them.

| # | Item | Approach | How it will be measured |
| --- | --- | --- | --- |
| 1 | **Live LLMs** (Groq / Gemini / OpenRouter free tiers) | Wired and tested end to end against a mock OpenAI-compatible server (JSON-mode fallback, 429 waits, verifier-forced rewrites, separate report, per-mode calibration). Remaining: run with a real key | Golden-set accuracy, verified-sentence rate, rewrite rate, cost and latency: LLM vs offline, paired bootstrap |
| 2 | **Real embeddings + reranker** | `bge-small` / `nomic-embed` behind the existing embedder interface (`CORTEX_EMBEDDER=st`); `bge-reranker` on the fused top 20 | Recall@3 and MRR vs current BM25 / LSA / hybrid; the NVIDIA–TSMC miss as a named case |
| 3 | **LLM-as-judge** | A rubric per answer type, a judge from a different model family than the writer, and randomised order in pairwise comparisons | Cohen's κ against ~50 answers graded by hand; ship only if κ ≥ 0.6 |
| 4 | **Native function-calling agent** | The model chooses MCP tools in a loop, inside the existing budgets, policy checks, checkpoints and approval gate | Task success, steps per task, cost per task vs fixed decomposition |
| 5 | **LoRA planner** | Distil intent labels from a frontier model; LoRA rank 8–16 on a 1–3B model on a free Kaggle/Colab GPU; 4-bit GGUF served by llama.cpp as a router arm | Accuracy and macro-F1 vs the frontier model; latency and cost per call |
| 6 | **Free deployment** | The topology in [deployment.md](deployment.md) | Public URL, p95 latency under load (k6), monthly cost |
| 7 | **Context studies** | Pad context with irrelevant passages at 2k / 8k / 32k tokens; compare evidence ordering strategies | Accuracy vs context-size curve; prompt-cache hit rate and cost |
| 8 | **Online evaluation** | Judge-score 5–10% of live traffic; detect drift in query embeddings with MMD and a permutation threshold | Daily quality trend; drift alerts |
| 9 | **Third domain (legal, CUAD)** | A new pack: clause extraction over 510 contracts with expert labels | Clause-level F1; no core code changes (tests ADR-017) |
| 10 | **Keycloak OIDC** | A JWT role claim feeds the existing policy engine | RLS probe passes with real tokens |
