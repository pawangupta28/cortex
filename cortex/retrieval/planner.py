"""Query planner: decide which retrievers a question needs and extract its filters.

Offline it uses transparent rules; with an LLM key it asks the model for a JSON plan and
keeps the rule-based plan as a fallback. This is the slot for the LoRA fine-tuned small
classifier (training/ notebook): it is a narrow, high-volume task where a 1-3B model can
match a frontier model at a fraction of the cost and latency.
"""
from __future__ import annotations

import re

from cortex.core.models import Intent, Plan
from cortex.domain import get_domain
from cortex.gateway.gateway import Gateway, parse_json
from cortex.retrieval.semantic_layer import SemanticLayer

WHY_CUES = r"\b(why|reason|reasons|cause|causes|caused|complain\w*|what do customers say|feedback|themes?|driv\w+|drove|explain\w*|behind)\b"
FORECAST_CUES = r"\b(will|forecast|predict\w*|next year|projected?|expect\w*)\b"


class Planner:
    def __init__(self, gateway: Gateway, layer: SemanticLayer | None = None):
        self.gw = gateway
        self.layer = layer or SemanticLayer()
        self.domain = get_domain()

    def rule_plan(self, question: str) -> Plan:
        q = question.lower()
        spec = self.layer.parse(question)
        doc = bool(re.search(self.domain.doc_cues, q))
        why = bool(re.search(WHY_CUES, q))
        filters = dict(spec.filters) if spec else {}
        for name, vals in self.layer.find_entities(question).items():
            filters.setdefault(name, vals if len(vals) > 1 else vals[0])
        filters["reviews"] = bool(re.search(self.domain.opinion_cues, q)) or why
        missing = self.layer.unavailable_mentions(question)
        if missing:
            filters["unavailable"] = missing
            return Plan(intent=Intent.UNSUPPORTED, confidence=0.9, filters=filters)   # entity not in this dataset
        if spec and re.search(FORECAST_CUES, q):
            filters["forecast"] = True
            return Plan(intent=Intent.UNSUPPORTED, confidence=0.8, filters=filters)   # no forecasting model yet
        if spec and why:
            return Plan(intent=Intent.HYBRID, confidence=0.85, filters=filters,
                        sub_questions=[question, f"customer reviews explaining: {question}"])
        if spec and not doc:
            return Plan(intent=Intent.METRIC, confidence=spec.confidence, filters=filters)
        if doc or why or filters.get("reviews"):
            return Plan(intent=Intent.DOCS, confidence=0.8 if doc else 0.65, filters=filters)
        if spec:
            return Plan(intent=Intent.HYBRID, confidence=0.6, filters=filters)
        return Plan(intent=Intent.UNSUPPORTED, confidence=0.9, filters=filters)

    def plan(self, question: str) -> Plan:
        base = self.rule_plan(question)
        if not self.gw.online or base.filters.get("unavailable"):
            return base
        res = self.gw.complete("plan", [
            {"role": "system", "content": f"Classify the question for an analytics assistant over: {self.domain.title}. "
             "intent is one of: metric (numbers from the warehouse), docs (answered from documents), "
             "hybrid (numbers plus reasons from documents), unsupported (outside this data). "
             "Return JSON {\"intent\": str, \"confidence\": float}."},
            {"role": "user", "content": question}], json_mode=True,
            offline=lambda: '{"intent": "%s", "confidence": %s}' % (base.intent.value, base.confidence))
        data = parse_json(res.text) or {}
        try:
            intent = Intent(data.get("intent", base.intent.value))
        except ValueError:
            return base
        agree = intent == base.intent
        self.gw.feedback(res, 1.0 if agree else 0.5)
        return Plan(intent=intent, confidence=min(0.97, float(data.get("confidence", 0.8)) + (0.05 if agree else -0.1)),
                    filters=base.filters, sub_questions=base.sub_questions)
