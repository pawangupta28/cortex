"""Layer 5: multi-step agent tasks as a durable state machine.

    planned -> researching -> drafting -> verifying -> awaiting_approval -> acting -> done
                                   \\-> needs_human          (budget exceeded, verification failed)
                                                         \\-> failed (unrecoverable error)

* Durable: state is checkpointed to SQLite after every step; `run(task_id)` resumes a
  crashed task from its last completed step instead of starting over.
* Bounded: max steps, wall time and cost per task; repeated identical steps are detected.
* Governed: the only agent that can write to business systems is the actor, it runs only
  after a human approves, every call passes the policy engine, and every write carries an
  idempotency key. Multi-system writes are a saga: completed steps are undone in reverse
  if a later step fails.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass

from cortex.agents.pipeline import Cortex
from cortex.config import settings
from cortex.gateway.gateway import parse_json
from cortex.telemetry.tracer import current_trace_id, span

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, goal TEXT, role TEXT, state TEXT, plan TEXT, steps TEXT, report TEXT,
  proposed_action TEXT, approval TEXT, error TEXT, cost REAL DEFAULT 0, trace_id TEXT,
  created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS crm_tickets (
  id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE, title TEXT, body TEXT, created REAL);
CREATE TABLE IF NOT EXISTS notifications (
  id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE, channel TEXT, text TEXT, created REAL);
"""


@dataclass
class Budget:
    max_steps: int = 8
    max_seconds: float = 180.0
    max_cost_usd: float = 0.05


class SimulatedCrash(RuntimeError):
    """Raised in tests to prove resume-from-checkpoint works."""


class Orchestrator:
    def __init__(self, cortex: Cortex | None = None, db_path=None, budget: Budget | None = None):
        self.cx = cortex or Cortex()
        self.db_path = str(db_path or settings.state_db)
        settings.state_db.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(SCHEMA)
        self.budget = budget or Budget()
        self.fail_notification = False      # chaos switch for the saga test

    def _db(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        return con

    # ------------------------------------------------------------- persistence
    def _save(self, t: dict) -> None:
        t["updated"] = time.time()
        with self._db() as db:
            db.execute("""INSERT OR REPLACE INTO tasks VALUES (:id,:goal,:role,:state,:plan,:steps,:report,
                          :proposed_action,:approval,:error,:cost,:trace_id,:created,:updated)""",
                       {**t, "plan": json.dumps(t["plan"]), "steps": json.dumps(t["steps"]),
                        "proposed_action": json.dumps(t["proposed_action"]), "approval": json.dumps(t["approval"])})

    def get(self, task_id: str) -> dict | None:
        with self._db() as db:
            r = db.execute("SELECT * FROM tasks WHERE id=?", [task_id]).fetchone()
        if not r:
            return None
        t = dict(r)
        for k in ("plan", "steps", "proposed_action", "approval"):
            t[k] = json.loads(t[k]) if t[k] else None
        return t

    def create(self, goal: str, role: str = "analyst") -> dict:
        t = {"id": uuid.uuid4().hex[:12], "goal": goal, "role": role, "state": "planned", "plan": None,
             "steps": [], "report": None, "proposed_action": None, "approval": None, "error": None,
             "cost": 0.0, "trace_id": "", "created": time.time(), "updated": time.time()}
        self._save(t)
        return t

    # ------------------------------------------------------------- planning
    def _decompose(self, goal: str) -> list[str]:
        layer = self.cx.layer
        spec = layer.parse(goal + " revenue")
        f = spec.filters if spec else {}
        cat = f.get("category")
        cat = cat[0] if isinstance(cat, list) else cat
        st = f.get("state")
        st = st[0] if isinstance(st, list) else st
        where = (f" for {cat.replace('_', ' ')}" if cat else "") + (f" in {st}" if st else "")
        if cat:
            offline = [f"Quarterly revenue{where}", f"Late delivery rate{where} by quarter",
                       f"Average review score{where} by quarter",
                       f"Why do customers complain about {cat.replace('_', ' ')}?",
                       f"What are the operating notes and packaging rules for {cat.replace('_', ' ')}?"]
        else:
            offline = [f"Which product categories have the highest late delivery rate{where}?",
                       f"Which categories have the lowest average review score{where}?",
                       f"Quarterly revenue{where}", "Why do customers leave 1-star reviews?",
                       "What compensation do customers get for late orders?"]
        if not self.cx.gw.online:
            return offline
        res = self.cx.gw.complete("plan", [
            {"role": "system", "content": "Break the analyst's goal into 3-5 standalone questions answerable from an "
                                          "e-commerce warehouse (orders, revenue, deliveries, review scores), customer "
                                          "review text and store policies. Return JSON {\"questions\": [str]}."},
            {"role": "user", "content": goal}], json_mode=True, offline=lambda: json.dumps({"questions": offline}))
        qs = (parse_json(res.text) or {}).get("questions") or offline
        return [q for q in qs if isinstance(q, str)][: self.budget.max_steps] or offline

    # ------------------------------------------------------------- run / resume
    def run(self, task_id: str, crash_after_step: int | None = None) -> dict:
        t = self.get(task_id)
        if t is None:
            raise KeyError(task_id)
        if t["state"] in ("done", "failed", "awaiting_approval", "needs_human"):
            return t
        start = time.time()
        with span("agent.task", **{"cortex.task_id": task_id}) as sp:
            t["trace_id"] = t["trace_id"] or current_trace_id()
            if t["plan"] is None:
                t["plan"] = self._decompose(t["goal"])
                t["state"] = "researching"
                self._save(t)
            done_qs = {s["question"] for s in t["steps"]}
            seen: set[str] = set()
            for i, q in enumerate(t["plan"]):
                h = hashlib.sha1(q.lower().encode()).hexdigest()
                if q in done_qs:
                    continue                      # resumed: this step was checkpointed
                if h in seen:
                    continue                      # loop detection
                seen.add(h)
                if len(t["steps"]) >= self.budget.max_steps or time.time() - start > self.budget.max_seconds \
                        or t["cost"] > self.budget.max_cost_usd:
                    t["state"], t["error"] = "needs_human", "budget exceeded"
                    self._save(t)
                    return t
                with span("agent.step", **{"cortex.agent": "researcher", "cortex.step": i}):
                    a = self.cx.ask(q, role=t["role"])
                t["cost"] += a.cost_usd
                t["steps"].append({"question": q, "answer": a.text, "confidence": a.confidence,
                                   "abstained": a.abstained, "verified": all(c.verified for c in a.claims),
                                   "sql": a.sql, "evidence": [e.id for e in a.evidence], "trace_id": a.trace_id})
                self._save(t)                     # checkpoint
                if crash_after_step is not None and len(t["steps"]) == crash_after_step:
                    raise SimulatedCrash(f"crash injected after step {crash_after_step}")

            t["state"] = "drafting"
            answered = [s for s in t["steps"] if not s["abstained"]]
            lines = [f"# Findings: {t['goal']}", ""]
            for s in answered:
                lines += [f"## {s['question']}", s["answer"], ""]
            skipped = [s["question"] for s in t["steps"] if s["abstained"]]
            if skipped:
                lines += ["## Not answered (insufficient evidence)", *[f"- {q}" for q in skipped]]
            t["report"] = "\n".join(lines)

            t["state"] = "verifying"
            unverified = [s["question"] for s in answered if not s["verified"]]
            if unverified or not answered:
                t["state"], t["error"] = "needs_human", f"unverified steps: {unverified}" if unverified else "no answers"
                self._save(t)
                return t
            key = hashlib.sha1(f"{task_id}:create_crm_ticket".encode()).hexdigest()[:16]
            t["proposed_action"] = {"tool": "create_crm_ticket", "idempotency_key": key,
                                    "args": {"title": f"Analytics finding: {t['goal'][:80]}",
                                             "body": t["report"][:4000]},
                                    "then": {"tool": "notify", "channel": "#ops-analytics"}}
            t["state"] = "awaiting_approval"
            self._save(t)
            sp["attributes"]["cortex.task_state"] = t["state"]
            return t

    # ------------------------------------------------------------- approval + actor
    def decide(self, task_id: str, approver: str, approve: bool, note: str = "") -> dict:
        t = self.get(task_id)
        if t is None or t["state"] != "awaiting_approval":
            raise ValueError("task is not awaiting approval")
        t["approval"] = {"approver": approver, "approved": approve, "note": note, "at": time.time()}
        if not approve:
            t["state"] = "done"
            self._save(t)
            return t
        t["state"] = "acting"
        self._save(t)
        try:
            self._act(t)
            t["state"] = "done"
        except Exception as e:  # noqa: BLE001
            t["state"], t["error"] = "failed", f"action rolled back: {e}"
        self._save(t)
        return t

    def _act(self, t: dict) -> None:
        act = t["proposed_action"]
        policy = self.cx.policy
        undo: list = []
        with span("agent.act", **{"cortex.agent": "actor"}):
            policy.check(agent="actor", tool="create_crm_ticket", role=t["role"], args=act["args"])
            with self._db() as db:
                cur = db.execute("INSERT OR IGNORE INTO crm_tickets VALUES (?,?,?,?,?)",
                                 [uuid.uuid4().hex[:12], act["idempotency_key"], act["args"]["title"],
                                  act["args"]["body"], time.time()])
                created = cur.rowcount == 1
            if created:
                undo.append(lambda: self._db().execute("DELETE FROM crm_tickets WHERE idempotency_key=?",
                                                       [act["idempotency_key"]]).connection.commit())
            try:
                if self.fail_notification:
                    raise RuntimeError("notification service unavailable")
                with self._db() as db:
                    db.execute("INSERT OR IGNORE INTO notifications VALUES (?,?,?,?,?)",
                               [uuid.uuid4().hex[:12], act["idempotency_key"], act["then"]["channel"],
                                act["args"]["title"], time.time()])
            except Exception:
                for step in reversed(undo):      # saga compensation
                    step()
                raise

    def tickets(self) -> list[dict]:
        with self._db() as db:
            return [dict(r) for r in db.execute("SELECT id, idempotency_key, title, created FROM crm_tickets")]
