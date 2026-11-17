"""Export a labelled intent dataset for fine-tuning the small planner/router model (LoRA).

Labels come from the rule-based planner (or, when an LLM key is set, from the frontier
model: distillation). Review a sample by hand before training, and keep 15% held out.

    PYTHONPATH=. python training/export_dataset.py > training/intent.jsonl
"""
import json
import sys

from cortex.evals.golden import load
from cortex.gateway.gateway import Gateway
from cortex.retrieval.planner import Planner

planner = Planner(Gateway())
for item in load():
    plan = planner.plan(item["question"])
    sys.stdout.write(json.dumps({"text": item["question"], "label": plan.intent.value,
                                 "confidence": plan.confidence, "split": item["split"]}) + "\n")
