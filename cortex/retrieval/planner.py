"""Query planner: decide which retrievers a question needs and extract its filters.

Offline it uses transparent rules; with an LLM key it asks the model for a JSON plan and
keeps the rule-based plan as a fallback. This is the slot for the LoRA fine-tuned small
classifier (training/ notebook): it is a narrow, high-volume task where a 1-3B model can
match a frontier model at a fraction of the cost and latency.
"""
from __future__ import annotations

import re

from cortex.core.models import Intent, Plan
from cortex.gateway.gateway import Gateway, parse_json
from cortex.retrieval.semantic_layer import SemanticLayer

DOC_CUES = r"\b(polic(y|ies)|refunds?|returns?|warrant(y|ies)|vouchers?|compensat\w*|rules?|thresholds?|probation|" \
           r"requirements?|process(es)?|how do|how does|what happens|can i|allowed|must|sla|packaging|" \
           r"guidelines?|procedures?|escalat\w*|carriers?|shelf life|expir\w*|bulletin|notes?|say about|says about|" \
           r"defin\w*|considered|meaning|means|estimates?)\b"
WHY_CUES = r"\b(why|reason|reasons|cause|causes|complain\w*|what do customers say|feedback|themes?|driv\w+)\b"
REVIEW_CUES = r"\b(review|reviews|customers say|complain\w*|feedback|unhappy|dissatisf\w*)\b"


class Planner:
    def __init__(self, gateway: Gateway, layer: SemanticLayer | None = None):
        self.gw = gateway
        self.layer = layer or SemanticLayer()

    def rule_plan(self, question: str) -> Plan:
        q = question.lower()
        spec = self.layer.parse(question)
        doc = bool(re.search(DOC_CUES, q))
        why = bool(re.search(WHY_CUES, q))
        filters = dict(spec.filters) if spec else {}
        if not spec:
            tmp = self.layer.parse(question + " revenue")   # borrow the filter extractor
            filters = dict(tmp.filters) if tmp else {}
        filters["reviews"] = bool(re.search(REVIEW_CUES, q)) or why
        if spec and re.search(r"\b(will|forecast|predict\w*|next year|projected?)\b", q):
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
        if not self.gw.online:
            return base
        res = self.gw.complete("plan", [
            {"role": "system", "content": "Classify the question for an e-commerce analytics assistant. "
             "intent is one of: metric (numbers from the sales/orders warehouse), docs (store policies or "
             "customer review text), hybrid (numbers plus reasons from reviews/policies), unsupported (outside "
             "the store's data). Return JSON {\"intent\": str, \"confidence\": float}."},
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
