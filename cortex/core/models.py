"""Typed contracts passed between layers. Every layer speaks these, nothing else."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Chunk(BaseModel):
    id: str
    doc_id: str
    text: str
    section_path: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class Evidence(BaseModel):
    """One unit of grounding: a retrieved chunk, a SQL result or a computed value."""
    id: str
    kind: Literal["chunk", "sql_result", "computed"]
    content: str
    score: float = 0.0
    source: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class Intent(str, Enum):
    METRIC = "metric"        # answerable from the warehouse
    DOCS = "docs"            # answerable from documents / reviews
    HYBRID = "hybrid"        # needs both ("why did X drop?")
    UNSUPPORTED = "unsupported"


class Plan(BaseModel):
    intent: Intent
    confidence: float
    sub_questions: list[str] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)


class Claim(BaseModel):
    text: str
    numbers: list[float] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    verified: bool = False


class Answer(BaseModel):
    question: str
    text: str
    intent: Intent
    claims: list[Claim] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    sql: str | None = None
    confidence: float = 0.0
    abstained: bool = False
    abstain_reason: str | None = None
    trace_id: str = ""
    model: str = ""
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    cached: bool = False


class ContextItem(BaseModel):
    slot: Literal["system", "task", "evidence", "tool_result", "memory", "history", "examples"]
    content: str
    value: float = 1.0          # importance; used by the knapsack selector
    fixed: bool = False         # never evicted (system rules, output schema)
    ref: str = ""


class ContextManifest(BaseModel):
    budget: int
    used: int
    included: list[dict[str, Any]]
    evicted: list[dict[str, Any]]
