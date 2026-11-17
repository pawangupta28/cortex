"""Input/output guards: prompt-injection detection, spotlighting, PII redaction, tool policy.

Defence in depth for indirect prompt injection (OWASP LLM01):
  1. detect   - score user input and every retrieved chunk for injection patterns
  2. quarantine - chunks above threshold are dropped from context and logged
  3. spotlight - remaining untrusted text is wrapped in delimiters with datamarking,
                 and the system prompt says text inside them is data, never instructions
  4. least privilege - the model that reads untrusted text has no write tools; writes
                 need a separate, policy-checked, human-approved step (agents layer)
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from cortex.config import settings

INJECTION_PATTERNS = [
    (r"ignore (all |any )?(previous|prior|above) (instructions|prompts|rules)", 0.9),
    (r"disregard (the |all )?(system|previous) (prompt|instructions)", 0.9),
    (r"(reveal|print|show|repeat) (your |the )?(system prompt|hidden instructions|instructions)", 0.8),
    (r"system note to (ai|assistant|llm)s?", 0.7),
    (r"you are now (a|an|in) ", 0.6),
    (r"\b(drop|truncate|delete from|alter) table\b", 0.7),
    (r"(exfiltrate|send) (the )?(data|database|credentials|api key)", 0.8),
    (r"act as (an? )?(unrestricted|jailbroken|dan)", 0.8),
    (r"</?(system|assistant|untrusted)>", 0.6),
]
_COMPILED = [(re.compile(p, re.I), w) for p, w in INJECTION_PATTERNS]


@dataclass
class InjectionVerdict:
    score: float
    matches: list[str]

    @property
    def blocked(self) -> bool:
        return self.score >= 0.7


def injection_score(text: str) -> InjectionVerdict:
    """Noisy-OR over matched patterns: 1 - prod(1 - w_i). Independent weak signals add up."""
    matches, p = [], 1.0
    for rx, w in _COMPILED:
        if rx.search(text):
            matches.append(rx.pattern)
            p *= 1 - w
    return InjectionVerdict(score=round(1 - p, 3), matches=matches)


def spotlight(text: str, source: str) -> str:
    """Datamark untrusted text: wrap it and interleave a marker so instructions inside
    cannot pass as the operator's own text."""
    marked = text.replace("\n", "\n^ ")
    return f'<untrusted source="{source}">\n^ {marked}\n</untrusted>'


PII_PATTERNS = {   # order matters: most specific first
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
    "cpf": re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"),          # Brazilian tax id
    "card": re.compile(r"\b(?:\d{4}[ -]?){3}\d{1,4}\b"),
    "phone": re.compile(r"(\+?\d{1,3}[\s-]?)?\(?\d{2,3}\)?[\s-]?\d{4,5}[\s-]?\d{4}\b"),
    "uuid_id": re.compile(r"\b[0-9a-f]{32}\b"),                    # customer / order ids
}


def redact_pii(text: str, keep: tuple[str, ...] = ()) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    for name, rx in PII_PATTERNS.items():
        if name in keep:
            continue
        text, n = rx.subn(f"[{name.upper()}]", text)
        if n:
            counts[name] = n
    return text, counts


# ------------------------------------------------------------------ tool policy
class PolicyDenied(PermissionError):
    pass


class PolicyEngine:
    """Least-privilege tool access per agent and per user role, with an append-only audit log.

    This is the integration point for Rungate (pip install rungate-ai): if it is installed,
    every decision is also passed through its policy/rate-limit/kill-switch pipeline.
    """

    def __init__(self, path: Path | None = None):
        path = path or settings.domain_dir / "policies.yaml"
        cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.roles = cfg["roles"]
        self.agent_tools = cfg["agent_tools"]
        self.audit_path = settings.artifacts_dir / "audit.jsonl"
        self.killed = False
        try:  # optional
            import rungate  # noqa: F401
            self.rungate = True
        except Exception:
            self.rungate = False

    def role(self, role: str) -> dict:
        if role not in self.roles:
            raise PolicyDenied(f"unknown role {role!r}")
        return self.roles[role]

    def allowed_states(self, role: str):
        return self.role(role)["states"]

    def check(self, *, agent: str, tool: str, role: str, args: dict | None = None) -> None:
        decision, reason = "allow", ""
        if self.killed:
            decision, reason = "deny", "kill switch engaged"
        elif tool not in self.agent_tools.get(agent, []):
            decision, reason = "deny", f"agent {agent!r} may not call {tool!r}"
        elif tool not in self.role(role)["tools"] and tool not in ("compute", "get_evidence", "create_crm_ticket"):
            decision, reason = "deny", f"role {role!r} may not use {tool!r}"
        self._audit({"agent": agent, "tool": tool, "role": role, "decision": decision, "reason": reason,
                     "args": {k: str(v)[:200] for k, v in (args or {}).items()}})
        if decision == "deny":
            raise PolicyDenied(reason)

    def _audit(self, rec: dict) -> None:
        settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
        rec["ts"] = time.time()
        with self.audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
