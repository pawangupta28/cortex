# ADR-001: DuckDB for the MVP warehouse, Postgres + pgvector for production

Status: accepted · 2026-11-17

## Context
The MVP must run anywhere with zero services: a laptop, CI and a free VM.

## Options considered
1. Postgres + pgvector from day one: production-shaped, but needs a running server for every test.
2. DuckDB for analytics + in-process indexes: zero setup, very fast aggregates, single file.

## Decision
DuckDB for the warehouse and chunk store; the vector and BM25 indexes are in-process and pickled.

## Evidence
Full ingest of 10k orders, 11.7k items and 6.5k review chunks plus index build takes ~12 s; the eval suite (102 questions) runs in ~6 s.

## Consequences
Single-writer. The production path (blueprint section 3) moves chunks and vectors to Postgres + pgvector; the `HybridIndex` interface does not change.
