"""Domain packs: everything domain-specific lives in domains/<name>/, never in the core.

A pack is
  domain.yaml         configuration the core reads (tables, row-level security, document
                      collections, routing cues, agent templates, red-team cases)
  semantic_layer.yaml metrics, dimensions, entities and time handling
  glossary.yaml       entity names and synonyms
  policies.yaml       roles, data scopes and tool permissions
  pack.py             code that loads the domain's data and builds its golden test set
  docs/               (optional) reference documents

Adding a domain means adding a folder. The retrieval, SQL, agent, security, evaluation
and API layers do not change.
"""
from __future__ import annotations

import importlib.util
from functools import lru_cache
from pathlib import Path
from types import ModuleType

import yaml

from cortex.config import ROOT, settings


class Domain:
    def __init__(self, name: str):
        self.name = name
        self.dir: Path = ROOT / "domains" / name
        self.cfg: dict = yaml.safe_load((self.dir / "domain.yaml").read_text(encoding="utf-8"))
        spec = importlib.util.spec_from_file_location(f"cortex_domain_{name}", self.dir / "pack.py")
        self.pack: ModuleType = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.pack)

    # ---- configuration accessors (with safe defaults)
    @property
    def title(self) -> str:
        return self.cfg.get("title", self.name)

    @property
    def tables(self) -> set[str]:
        return set(self.cfg["tables"])

    @property
    def rls(self) -> dict:
        return self.cfg.get("rls", {})

    @property
    def collections(self) -> dict:
        return self.cfg.get("collections", {})

    @property
    def doc_cues(self) -> str:
        return r"\b(" + "|".join(self.cfg.get("doc_cues", [])) + r")\b" if self.cfg.get("doc_cues") else r"(?!x)x"

    @property
    def opinion_cues(self) -> str:
        cues = self.cfg.get("opinion_cues", [])
        return r"\b(" + "|".join(cues) + r")\b" if cues else r"(?!x)x"

    @property
    def entity_metadata(self) -> dict[str, str]:
        return self.cfg.get("entity_metadata", {})

    @property
    def investigation(self) -> dict:
        return self.cfg.get("investigation", {})

    @property
    def examples(self) -> list[str]:
        return self.cfg.get("examples", [])

    @property
    def fallback_message(self) -> str:
        return self.cfg.get("fallback_message", "Try a question about the data this system covers.")

    @property
    def redteam(self) -> list[dict]:
        return self.cfg.get("redteam", [])

    @property
    def rls_probe(self) -> dict | None:
        return self.cfg.get("rls_probe")

    # ---- code hooks
    def ingest(self, con, force_synthetic: bool = False):
        return self.pack.ingest(con, self, force_synthetic=force_synthetic)

    def golden(self, con) -> list[dict]:
        return self.pack.golden(con, self)


@lru_cache(maxsize=None)
def _load(name: str) -> Domain:
    return Domain(name)


def get_domain(name: str | None = None) -> Domain:
    return _load(name or settings.domain)
