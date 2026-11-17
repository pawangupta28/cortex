"""Runtime settings, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


@dataclass
class ProviderConfig:
    name: str
    base_url: str
    api_key: str
    model: str
    # Shadow price in USD per 1M tokens: free tiers cost 0, but the router
    # optimises against list prices so the design carries over to paid use.
    shadow_price_per_mtok: float


@dataclass
class Settings:
    domain: str = field(default_factory=lambda: _env("CORTEX_DOMAIN", "ecommerce"))
    data_dir: Path = field(default_factory=lambda: Path(_env("CORTEX_DATA_DIR", str(ROOT / "data"))))
    artifacts_dir: Path = field(default_factory=lambda: Path(_env("CORTEX_ARTIFACTS_DIR", str(ROOT / "artifacts"))))
    db_path: Path = field(default_factory=lambda: Path(_env("CORTEX_DB", str(ROOT / "data" / "cortex.duckdb"))))
    state_db: Path = field(default_factory=lambda: Path(_env("CORTEX_STATE_DB", str(ROOT / "data" / "state.sqlite"))))
    context_budget_tokens: int = int(_env("CORTEX_CONTEXT_BUDGET", "3000"))
    sql_row_limit: int = int(_env("CORTEX_SQL_ROW_LIMIT", "200"))
    sql_timeout_s: float = float(_env("CORTEX_SQL_TIMEOUT", "5"))
    cache_threshold: float = float(_env("CORTEX_CACHE_THRESHOLD", "0.97"))
    conformal_alpha: float = float(_env("CORTEX_CONFORMAL_ALPHA", "0.1"))

    @property
    def domain_dir(self) -> Path:
        return ROOT / "domains" / self.domain

    def providers(self) -> list[ProviderConfig]:
        """LLM providers enabled by the API keys present in the environment.

        All three expose OpenAI-compatible chat endpoints, so one adapter covers them.
        With no keys set, Cortex runs on its deterministic offline provider.
        """
        out: list[ProviderConfig] = []
        if _env("GROQ_API_KEY"):
            out.append(ProviderConfig("groq", "https://api.groq.com/openai/v1", _env("GROQ_API_KEY"),
                                      _env("GROQ_MODEL", "llama-3.3-70b-versatile"), 0.6))
        if _env("GEMINI_API_KEY"):
            out.append(ProviderConfig("gemini", "https://generativelanguage.googleapis.com/v1beta/openai",
                                      _env("GEMINI_API_KEY"), _env("GEMINI_MODEL", "gemini-2.5-flash"), 0.3))
        if _env("OPENROUTER_API_KEY"):
            out.append(ProviderConfig("openrouter", "https://openrouter.ai/api/v1", _env("OPENROUTER_API_KEY"),
                                      _env("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free"), 0.0))
        return out


settings = Settings()
