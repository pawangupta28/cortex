"""HTTP API + demo UI + MCP endpoint + A2A-style agent endpoint.

Auth in this MVP: the caller's role comes from the `X-Role` header and is validated
against domains/<domain>/policies.yaml. Production swaps this for OIDC (Keycloak) JWTs
whose role claim feeds the same policy engine; nothing downstream changes.
"""
from __future__ import annotations

import contextlib
import json
import uuid
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from cortex.agents.orchestrator import Orchestrator
from cortex.agents.pipeline import Cortex
from cortex.config import settings
from cortex.gateway.router import simulate
from cortex.security.guards import PolicyDenied
from cortex.telemetry.tracer import load_trace

STATIC = Path(__file__).parent / "static"


@lru_cache(maxsize=1)
def cortex() -> Cortex:
    return Cortex()


@lru_cache(maxsize=1)
def orchestrator() -> Orchestrator:
    return Orchestrator(cortex())


try:
    from cortex.api.mcp_server import mcp
    _mcp_app = mcp.streamable_http_app(streamable_http_path="/", stateless_http=True, json_response=True)
except Exception:  # pragma: no cover - MCP is optional
    _mcp_app = None


@contextlib.asynccontextmanager
async def lifespan(_app):
    if _mcp_app is not None:
        async with mcp.session_manager.run():
            yield
    else:
        yield


app = FastAPI(title="Cortex", version="0.1.0", lifespan=lifespan,
              description="Domain-agnostic enterprise GenAI platform (MVP)")
if _mcp_app is not None:
    app.mount("/mcp", _mcp_app)


def _role(x_role: str | None) -> str:
    role = x_role or "analyst"
    try:
        cortex().policy.role(role)
    except PolicyDenied as e:
        raise HTTPException(403, str(e)) from e
    return role


class AskIn(BaseModel):
    question: str
    use_cache: bool = True


class TaskIn(BaseModel):
    goal: str


class DecisionIn(BaseModel):
    approver: str
    approve: bool
    note: str = ""


@app.get("/")
def ui():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    cx = cortex()
    return {"status": "ok", "mode": "online" if cx.gw.online else "offline-deterministic",
            "providers": list(cx.gw.providers), "data_version": cx.data_version,
            "chunks": {k: len(v.chunks) for k, v in cx.corpus.collections.items()},
            "cache": cx.cache.stats()}


@app.post("/v1/ask")
def ask(body: AskIn, x_role: str | None = Header(default=None)):
    role = _role(x_role)
    try:
        a = cortex().ask(body.question, role=role, use_cache=body.use_cache)
    except PolicyDenied as e:
        raise HTTPException(403, str(e)) from e
    out = a.model_dump()
    out["signals"] = a.__dict__.get("signals", {})
    return out


@app.post("/v1/tasks")
def create_task(body: TaskIn, x_role: str | None = Header(default=None)):
    role = _role(x_role)
    o = orchestrator()
    t = o.create(body.goal, role)
    return o.run(t["id"])


@app.get("/v1/tasks/{task_id}")
def get_task(task_id: str):
    t = orchestrator().get(task_id)
    if not t:
        raise HTTPException(404, "no such task")
    return t


@app.post("/v1/tasks/{task_id}/decision")
def decide(task_id: str, body: DecisionIn):
    try:
        return orchestrator().decide(task_id, body.approver, body.approve, body.note)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@app.get("/v1/crm/tickets")
def tickets():
    return orchestrator().tickets()


@app.get("/v1/traces/{trace_id}")
def trace(trace_id: str):
    return load_trace(trace_id)


@app.get("/v1/evals/latest")
def evals():
    p = settings.artifacts_dir / "eval_report.json"
    if not p.exists():
        raise HTTPException(404, "run `python -m cortex.cli eval` first")
    return JSONResponse(json.loads(p.read_text(encoding="utf-8")))


@app.get("/v1/router/simulation")
def router_sim():
    return simulate()


# ---------------------------------------------------------------- A2A-style agent endpoint
AGENT_CARD = {
    "name": "Cortex Research Agent",
    "description": "Answers e-commerce business questions with cited evidence, and runs multi-step investigations "
                   "that end in a human-approved CRM ticket.",
    "url": "http://localhost:8000/a2a",
    "version": "0.1.0",
    "capabilities": {"streaming": False, "pushNotifications": False},
    "defaultInputModes": ["text/plain"],
    "defaultOutputModes": ["text/plain", "application/json"],
    "skills": [
        {"id": "ask", "name": "Grounded business Q&A", "description": "One question, cited answer, confidence.",
         "tags": ["analytics", "rag", "text-to-sql"], "examples": ["Which categories have the highest late delivery rate?"]},
        {"id": "investigate", "name": "Multi-step investigation",
         "description": "Decomposes a goal, researches each part, drafts a verified report.",
         "tags": ["agents", "reporting"], "examples": ["Investigate why office furniture customers are unhappy"]},
    ],
}


@app.get("/.well-known/agent-card.json")
def agent_card():
    return AGENT_CARD


@app.post("/a2a")
def a2a(rpc: dict):
    """Minimal JSON-RPC `message/send` handler in the shape of the A2A protocol. It is a
    subset for the demo; validate against the official A2A SDK before claiming conformance."""
    if rpc.get("method") != "message/send":
        return {"jsonrpc": "2.0", "id": rpc.get("id"), "error": {"code": -32601, "message": "method not found"}}
    parts = rpc.get("params", {}).get("message", {}).get("parts", [])
    text = " ".join(p.get("text", "") for p in parts if p.get("kind", p.get("type")) == "text").strip()
    skill = rpc.get("params", {}).get("metadata", {}).get("skill", "ask")
    if skill == "investigate":
        o = orchestrator()
        t = o.run(o.create(text)["id"])
        result_text, state = t["report"] or t["error"] or "", t["state"]
    else:
        a = cortex().ask(text)
        result_text, state = a.text, "completed"
    return {"jsonrpc": "2.0", "id": rpc.get("id"), "result": {
        "kind": "task", "id": uuid.uuid4().hex, "status": {"state": "completed" if state != "needs_human" else "input-required"},
        "artifacts": [{"artifactId": uuid.uuid4().hex, "parts": [{"kind": "text", "text": result_text}]}]}}
