# ADR-017: Domain packs — the core never names a domain

Status: accepted · 2026-11-17

## Context
The MVP hard-coded e-commerce concepts in the planner (cue words), pipeline (policy/review collections), SQL guard (state-based row filters), orchestrator (category templates) and evals. A second domain would have meant forking the core.

## Decision
Everything domain-specific moved to `domains/<name>/`: `domain.yaml` (tables, row-level-security CTEs, document collections with roles, routing cues, section hints, agent templates, red-team cases), `semantic_layer.yaml` (metrics, entities, time/recency handling, output formats), `glossary.yaml` (entities and known-but-unavailable entities), `policies.yaml` (roles, scopes, tools) and `pack.py` (`ingest`, `golden`). Each domain gets its own warehouse, state store, artifacts and eval folder.

## Evidence
The finance pack (real SEC data) was added without domain-specific code in `cortex/`. E-commerce accuracy after the refactor: 97.1% (98.0% before). The single changed answer comes from the stricter lexical probe (ADR-019), not from the refactor itself.

## Consequences
A new domain is a folder plus a golden set. The YAML is the reviewable contract between data owners and the platform.
