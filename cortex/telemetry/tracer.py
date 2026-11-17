"""Minimal tracer following OpenTelemetry GenAI semantic-convention attribute names.

Spans are written as JSON lines to artifacts/traces.jsonl so every answer can be replayed.
If `opentelemetry-sdk` is installed and OTEL_EXPORTER_OTLP_ENDPOINT is set, spans are
mirrored to a real OTel exporter (Langfuse, Grafana, Jaeger) without code changes.
"""
from __future__ import annotations

import contextvars
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from cortex.config import settings

_current: contextvars.ContextVar[dict | None] = contextvars.ContextVar("cortex_span", default=None)
_lock = threading.Lock()

_otel_tracer = None
if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
    try:  # optional dependency
        from opentelemetry import trace as _ot
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        _provider = TracerProvider()
        _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        _ot.set_tracer_provider(_provider)
        _otel_tracer = _ot.get_tracer("cortex")
    except Exception:  # pragma: no cover - exporter is optional
        _otel_tracer = None


def _trace_file() -> Path:
    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    return settings.artifacts_dir / "traces.jsonl"


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[dict]:
    parent = _current.get()
    rec = {
        "trace_id": parent["trace_id"] if parent else uuid.uuid4().hex,
        "span_id": uuid.uuid4().hex[:16],
        "parent_id": parent["span_id"] if parent else None,
        "name": name,
        "start": time.time(),
        "attributes": dict(attrs),
        "status": "ok",
    }
    token = _current.set(rec)
    otel_cm = _otel_tracer.start_as_current_span(name) if _otel_tracer else None
    otel_span = otel_cm.__enter__() if otel_cm else None
    try:
        yield rec
    except Exception as exc:
        rec["status"] = "error"
        rec["attributes"]["error.type"] = type(exc).__name__
        raise
    finally:
        rec["end"] = time.time()
        rec["duration_ms"] = round((rec["end"] - rec["start"]) * 1000, 2)
        if otel_span is not None:
            for k, v in rec["attributes"].items():
                if isinstance(v, (str, int, float, bool)):
                    otel_span.set_attribute(k, v)
            otel_cm.__exit__(None, None, None)
        _current.reset(token)
        with _lock, _trace_file().open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")


def current_trace_id() -> str:
    cur = _current.get()
    return cur["trace_id"] if cur else ""


def set_attr(**attrs: Any) -> None:
    cur = _current.get()
    if cur is not None:
        cur["attributes"].update(attrs)


def load_trace(trace_id: str) -> list[dict]:
    path = _trace_file()
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec["trace_id"] == trace_id:
                out.append(rec)
    return sorted(out, key=lambda r: r["start"])
