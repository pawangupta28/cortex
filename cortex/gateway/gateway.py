"""Layer 4: the internal LLM gateway. Every model call in Cortex goes through `Gateway.complete`.

Responsibilities: provider adapters (OpenAI-compatible), routing (bandit), retries with
jittered exponential backoff, per-provider circuit breakers, a fallback chain, usage and
shadow-cost accounting, and graceful degradation to a deterministic offline path.
"""
from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass
from typing import Callable

import httpx

from cortex.config import ProviderConfig, settings
from cortex.gateway.router import BanditRouter
from cortex.telemetry.tracer import span


class ProviderError(RuntimeError):
    def __init__(self, msg: str, retry_after: float | None = None):
        super().__init__(msg)
        self.retry_after = retry_after


@dataclass
class LLMResult:
    text: str
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    latency_s: float
    cost_usd: float
    decision_id: str = ""


def estimate_tokens(text: str) -> int:
    # ~4 characters per token for English; providers report exact usage when online.
    return max(1, len(text) // 4)


class OpenAICompatProvider:
    def __init__(self, cfg: ProviderConfig, timeout: float = 60.0):
        self.cfg = cfg
        self.client = httpx.Client(timeout=timeout)
        self.json_mode_ok = True              # many free models reject response_format; learn it once
        self.min_interval = float(os.environ.get("CORTEX_MIN_CALL_INTERVAL", "0"))
        self._last_call = 0.0

    def chat(self, messages: list[dict], json_mode: bool = False, max_tokens: int = 800,
             temperature: float = 0.0) -> LLMResult:
        body = {"model": self.cfg.model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if json_mode and self.json_mode_ok:
            body["response_format"] = {"type": "json_object"}
        wait = self.min_interval - (time.time() - self._last_call)     # client-side pacing for free tiers
        if wait > 0:
            time.sleep(wait)
        t0 = time.time()
        self._last_call = t0
        try:
            r = self.client.post(f"{self.cfg.base_url}/chat/completions", json=body,
                                 headers={"Authorization": f"Bearer {self.cfg.api_key}",
                                          "HTTP-Referer": "https://github.com/pawangupta28/cortex", "X-Title": "Cortex"})
        except httpx.HTTPError as e:
            raise ProviderError(f"{self.cfg.name}: {e}") from e
        if r.status_code == 400 and "response_format" in body:
            self.json_mode_ok = False                                  # retry once without JSON mode
            return self.chat(messages, json_mode=False, max_tokens=max_tokens, temperature=temperature)
        if r.status_code == 429:
            ra = r.headers.get("retry-after")
            raise ProviderError(f"{self.cfg.name}: rate limited", retry_after=float(ra) if ra and ra.replace(".", "").isdigit() else 20.0)
        if r.status_code >= 400:
            raise ProviderError(f"{self.cfg.name}: HTTP {r.status_code} {r.text[:200]}")
        data = r.json()
        if "choices" not in data:
            raise ProviderError(f"{self.cfg.name}: unexpected response {str(data)[:200]}")
        text = data["choices"][0]["message"].get("content") or ""
        usage = data.get("usage", {})
        it = usage.get("prompt_tokens", estimate_tokens(json.dumps(messages)))
        ot = usage.get("completion_tokens", estimate_tokens(text))
        cost = (it + ot) / 1e6 * self.cfg.shadow_price_per_mtok
        return LLMResult(text, self.cfg.model, self.cfg.name, it, ot, time.time() - t0, cost)


class CircuitBreaker:
    """Closed -> open after `threshold` consecutive failures; half-open after `cooldown_s`."""

    def __init__(self, threshold: int = 3, cooldown_s: float = 60.0):
        self.threshold, self.cooldown = threshold, cooldown_s
        self.failures = 0
        self.opened_at: float | None = None

    @property
    def available(self) -> bool:
        if self.opened_at is None:
            return True
        return time.time() - self.opened_at >= self.cooldown      # half-open: allow a probe

    def success(self) -> None:
        self.failures, self.opened_at = 0, None

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = time.time()


class Gateway:
    def __init__(self, providers: list | None = None, retries: int = 2):
        cfgs = settings.providers() if providers is None else providers
        self.providers = {p.cfg.name if hasattr(p, "cfg") else p.name: p if hasattr(p, "chat") else OpenAICompatProvider(p)
                          for p in cfgs}
        self.breakers = {n: CircuitBreaker() for n in self.providers}
        self.retries = retries
        # per-provider call and token counters: lets the eval prove which answers came from a live model
        self.calls: dict[str, int] = {}
        self.tokens: dict[str, int] = {}
        self.router = BanditRouter(list(self.providers) or ["offline"],
                                   state_path=settings.artifacts_dir / "router_state.json")
        self.router.load()

    @property
    def online(self) -> bool:
        return bool(self.providers)

    def complete(self, task: str, messages: list[dict], *, offline: Callable[[], str] | None = None,
                 json_mode: bool = False, max_tokens: int = 800) -> LLMResult:
        prompt = json.dumps(messages)
        with span("gen_ai.chat", **{"gen_ai.operation.name": "chat", "cortex.task": task}) as sp:
            if self.providers:
                available = [n for n, b in self.breakers.items() if b.available]
                if available:
                    first, did = self.router.choose(task, estimate_tokens(prompt), available)
                    chain = [first] + [n for n in available if n != first]
                    for name in chain:
                        res = self._try(name, messages, json_mode, max_tokens)
                        if res is not None:
                            res.decision_id = did if name == first else ""
                            sp["attributes"].update({
                                "gen_ai.system": res.provider, "gen_ai.request.model": res.model,
                                "gen_ai.usage.input_tokens": res.input_tokens,
                                "gen_ai.usage.output_tokens": res.output_tokens,
                                "cortex.cost_usd": round(res.cost_usd, 6), "cortex.fallback": name != first})
                            self._count(name, res)
                            return res
            if offline is None:
                raise ProviderError("no provider available and no offline fallback")
            t0 = time.time()
            text = offline()
            sp["attributes"].update({"gen_ai.system": "offline", "gen_ai.request.model": "offline-deterministic"})
            res = LLMResult(text, "offline-deterministic", "offline", estimate_tokens(prompt),
                            estimate_tokens(text), time.time() - t0, 0.0)
            if self.providers:
                self._count("offline", res)      # a live run fell back: the eval must not credit this to a model
            return res

    def _count(self, name: str, res: LLMResult) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1
        self.tokens[name] = self.tokens.get(name, 0) + res.input_tokens + res.output_tokens

    def _try(self, name: str, messages, json_mode, max_tokens) -> LLMResult | None:
        prov = self.providers[name]
        for attempt in range(self.retries + 1):
            try:
                res = prov.chat(messages, json_mode=json_mode, max_tokens=max_tokens)
                self.breakers[name].success()
                return res
            except ProviderError as e:
                if e.retry_after is not None and attempt < self.retries:
                    time.sleep(min(e.retry_after, 60.0))     # rate limit: wait as told, not a provider fault
                    continue
                self.breakers[name].failure()
                if not self.breakers[name].available:
                    return None
                time.sleep(min(4.0, 0.5 * 2 ** attempt) * random.uniform(0.5, 1.0))
        return None

    def feedback(self, res: LLMResult, quality: float) -> None:
        """Close the loop: verifier / judge outcomes become bandit rewards."""
        if res.decision_id:
            self.router.update(res.decision_id, quality, res.cost_usd, res.latency_s)
            self.router.save()


def parse_json(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]
    try:
        start, end = text.find("{"), text.rfind("}")
        return json.loads(text[start:end + 1]) if start >= 0 else None
    except json.JSONDecodeError:
        return None
