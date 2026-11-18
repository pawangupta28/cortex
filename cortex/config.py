"""Runtime settings, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path = ROOT / ".env") -> None:
    """Minimal .env reader (KEY=VALUE lines). Real environment variables always win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v


if not os.environ.get("CORTEX_NO_DOTENV"):
    _load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


PLACEHOLDER_PREFIXES = ("your_", "your-", "<", "sk-xxx", "changeme")


def _key(name: str) -> str:
    """An API key, or "" if unset or still a template placeholder such as `your_groq_key`."""
    v = _env(name).strip()
    return "" if not v or v.lower().startswith(PLACEHOLDER_PREFIXES) else v


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
    artifacts_root: Path = field(default_factory=lambda: Path(_env("CORTEX_ARTIFACTS_DIR", str(ROOT / "artifacts"))))
    context_budget_tokens: int = int(_env("CORTEX_CONTEXT_BUDGET", "3000"))
    sql_row_limit: int = int(_env("CORTEX_SQL_ROW_LIMIT", "200"))
    sql_timeout_s: float = float(_env("CORTEX_SQL_TIMEOUT", "5"))
    cache_threshold: float = float(_env("CORTEX_CACHE_THRESHOLD", "0.97"))
    conformal_alpha: float = float(_env("CORTEX_CONFORMAL_ALPHA", "0.1"))

    # Every domain gets its own warehouse, state store and artifacts, so domains coexist.
    @property
    def domain_dir(self) -> Path:
        return ROOT / "domains" / self.domain

    @property
    def artifacts_dir(self) -> Path:
        return self.artifacts_root / self.domain

    @property
    def db_path(self) -> Path:
        return self.data_dir / f"{self.domain}.duckdb"

    @property
    def state_db(self) -> Path:
        return self.data_dir / f"{self.domain}_state.sqlite"

    def use_domain(self, name: str) -> None:
        if not (ROOT / "domains" / name / "domain.yaml").exists():
            raise ValueError(f"unknown domain {name!r}; available: {available_domains()}")
        self.domain = name

    def providers(self) -> list[ProviderConfig]:
        """LLM providers enabled by the API keys present in the environment.

        All three expose OpenAI-compatible chat endpoints, so one adapter covers them.
        With no keys set, Cortex runs on its deterministic offline provider.
        """
        out: list[ProviderConfig] = []
        if _key("GROQ_API_KEY"):
            out.append(ProviderConfig("groq", _env("GROQ_BASE_URL", "https://api.groq.com/openai/v1"), _key("GROQ_API_KEY"),
                                      _env("GROQ_MODEL", "openai/gpt-oss-120b"), 0.6))
        if _key("GEMINI_API_KEY"):
            out.append(ProviderConfig("gemini", _env("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai"),
                                      _key("GEMINI_API_KEY"), _env("GEMINI_MODEL", "gemini-2.5-flash"), 0.3))
        if _key("OPENROUTER_API_KEY"):
            out.append(ProviderConfig("openrouter", _env("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"), _key("OPENROUTER_API_KEY"),
                                      _env("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free"), 0.0))
        return out


settings = Settings()


def available_domains() -> list[str]:
    return sorted(p.parent.name for p in (ROOT / "domains").glob("*/domain.yaml"))
