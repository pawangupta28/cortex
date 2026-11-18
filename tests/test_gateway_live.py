"""The live-LLM path without network: fake providers exercise fallback, rate-limit waits,
offline degradation and .env loading."""
import os

from cortex.config import _load_dotenv
from cortex.gateway.gateway import Gateway, LLMResult, ProviderError


class Fake:
    def __init__(self, name, script):
        self.name, self.script, self.calls = name, list(script), 0

    def chat(self, messages, json_mode=False, max_tokens=800):
        self.calls += 1
        step = self.script.pop(0) if self.script else "ok"
        if isinstance(step, Exception):
            raise step
        return LLMResult(f"{self.name}:{step}", self.name, self.name, 10, 5, 0.01, 0.0)


def _gw(*providers):
    gw = Gateway(providers=[])
    gw.providers = {p.name: p for p in providers}
    from cortex.gateway.gateway import CircuitBreaker
    gw.breakers = {p.name: CircuitBreaker() for p in providers}
    from cortex.gateway.router import BanditRouter
    gw.router = BanditRouter([p.name for p in providers])
    return gw


def test_rate_limit_is_waited_out_not_counted_as_failure():
    a = Fake("a", [ProviderError("429", retry_after=0.01), "ok"])
    res = _gw(a).complete("write", [{"role": "user", "content": "x"}])
    assert res.text == "a:ok" and a.calls == 2


def test_falls_back_to_second_provider_then_offline():
    a = Fake("a", [ProviderError("down")] * 10)
    b = Fake("b", ["ok"])
    gw = _gw(a, b)
    res = gw.complete("write", [{"role": "user", "content": "x"}])
    assert res.provider in ("a", "b") and res.text.endswith("ok")
    dead = _gw(Fake("c", [ProviderError("down")] * 10))
    out = dead.complete("write", [{"role": "user", "content": "x"}], offline=lambda: "offline answer")
    assert out.provider == "offline" and out.text == "offline answer"


def test_dotenv_loads_without_overriding_real_env(tmp_path):
    env = tmp_path / ".env"
    env.write_text('CORTEX_TEST_A="from-file"\nCORTEX_TEST_B=file\n# comment\n', encoding="utf-8")
    os.environ["CORTEX_TEST_B"] = "real"
    _load_dotenv(env)
    assert os.environ["CORTEX_TEST_A"] == "from-file" and os.environ["CORTEX_TEST_B"] == "real"


def test_offline_fallback_is_counted_so_eval_never_credits_it_to_a_model():
    from cortex.evals.harness import live_stats
    a = Fake("a", ["ok", ProviderError("quota")] + [ProviderError("quota")] * 10)
    gw = _gw(a)
    gw.retries = 0
    msgs = [{"role": "user", "content": "x"}]
    assert gw.complete("write", msgs, offline=lambda: "o").provider == "a"
    for _ in range(4):
        gw.complete("write", msgs, offline=lambda: "o")
    assert gw.calls["a"] == 1 and gw.calls["offline"] == 4
    recs = [{"type": "metric", "correct": True, "live_calls": 1, "offline_calls": 0},
            {"type": "metric", "correct": True, "live_calls": 1, "offline_calls": 2},
            {"type": "docs", "correct": False, "live_calls": 0, "offline_calls": 1}]
    st = live_stats(recs, gw)
    assert st["fully_live_questions"] == 1 and st["live_coverage"] == round(1 / 3, 3)
    assert st["accuracy_fully_live"][0] == 1.0


def test_placeholder_keys_do_not_enable_a_provider(monkeypatch):
    from cortex.config import settings
    monkeypatch.setenv("OPENROUTER_API_KEY", "your_openrouter_key")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert settings.providers() == []
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "a" * 52)
    assert [p.name for p in settings.providers()] == ["groq"]


def test_reasoning_models_get_low_effort_and_headroom_and_survive_rejection():
    import json as _json
    import httpx
    from cortex.config import ProviderConfig
    from cortex.gateway.gateway import OpenAICompatProvider
    seen = []

    def handler(req):
        body = _json.loads(req.content)
        seen.append(body)
        if len(seen) == 1:
            return httpx.Response(400, json={"error": {"message": "unknown parameter reasoning_effort"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}], "usage": {}})

    p = OpenAICompatProvider(ProviderConfig("groq", "https://x/v1", "k", "openai/gpt-oss-120b", 0.0))
    p.client = httpx.Client(transport=httpx.MockTransport(handler))
    assert p.chat([{"role": "user", "content": "hi"}], max_tokens=10).text == "OK"
    assert seen[0]["reasoning_effort"] == "low" and seen[0]["max_tokens"] == 10 + 1024
    assert "reasoning_effort" not in seen[1] and seen[1]["max_tokens"] == 10
