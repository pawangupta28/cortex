# ADR-006: The model picks a query spec; the semantic layer writes the SQL

Status: accepted · 2026-11-17

## Context
The most common text-to-SQL failure is plausible but wrong aggregation (wrong join grain, wrong denominator).

## Decision
Metrics are defined once in `semantic_layer.yaml`. The LLM (or the offline parser) outputs a typed QuerySpec — metric, dimensions, filters, order, limit — that compiles deterministically to SQL. Raw LLM SQL is used only outside the layer and must pass the same guard.

## Evidence
54 single-value and 8 ranking/trend questions, graded against gold SQL written on the base tables (not the view), all pass in the current report.

## Consequences
New metrics need a YAML entry, which is a feature: metric definitions get reviewed like code.
