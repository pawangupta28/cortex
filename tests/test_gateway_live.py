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
