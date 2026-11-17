# Small-model fine-tuning (LoRA) — not run in this MVP

The planner's intent classification is a narrow, high-volume task: the right place for a
1–3B model fine-tuned with LoRA instead of a frontier model.

1. `PYTHONPATH=. python training/export_dataset.py > training/intent.jsonl` (labels from the planner, or
   from a frontier model when an API key is set — distillation). Hand-check ~300 rows.
2. Train on a free Kaggle or Colab GPU with Unsloth: LoRA rank 8–16 on a 1–3B instruct model,
   3 epochs, hold out 15%.
3. Export to GGUF (4-bit), serve with llama.cpp on CPU, and register it as a provider in
   `cortex/config.py` (any OpenAI-compatible endpoint works, e.g. `llama-server`).
4. Report accuracy, macro-F1, latency and cost against the frontier model on the holdout.

LoRA freezes W and learns a low-rank update: W' = W + BA, with B ∈ R^(d×r), A ∈ R^(r×k), r ≪ min(d, k).
