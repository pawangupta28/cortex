# 1. Ingestion and domain packs

**Status: Built · measured** (finance on real SEC data, e-commerce on Olist-schema data)

## Problem
Enterprise GenAI fails first at the data layer. Real sources are inconsistent: the same figure is labelled differently, periods are restated, documents mix tables, headers and prose. And a platform that hard-codes one business domain cannot be reused for the next client.

## Design

### Domain packs
Everything domain-specific lives in `domains/<name>/`. The core reads it; it never names a domain.

| File | Contents |
| --- | --- |
| `domain.yaml` | Allowed tables, row-level-security CTE templates, document collections and their roles (reference / opinion), routing cues, section hints, agent templates, red-team cases |
| `semantic_layer.yaml` | Metrics (SQL, format, direction), dimensions, entities, time and recency handling |
| `glossary.yaml` | Entity names and synonyms, plus known-but-unavailable entities |
| `policies.yaml` | Roles, data scopes, per-agent tool permissions |
| `pack.py` | `ingest(con)` → tables + chunks + lineage; `golden(con)` → test set |

Each domain gets its own warehouse, task store, artifacts and eval folder, so domains run side by side.

### Finance pack: what real XBRL data required
Source: SEC companyfacts JSON (every XBRL fact a company reported) + latest 10-K and 10-Q HTML, via the free SEC API.

| Problem in the raw data | Handling |
| --- | --- |
| Same line item, different concepts (NVIDIA `Revenues`, Apple `RevenueFromContractWithCustomer…`), and concept switches over time | Candidate concepts ranked by most recent period reported; each period takes the best concept that has it |
| The same period in many filings (restatements) | Latest filing wins |
| Fiscal Q4 only exists inside the annual total | Q4 = FY − (Q1+Q2+Q3) for additive metrics, flagged `q4_derived`; EPS not derived (not additive) |
| 10-Qs report trailing-twelve-month figures that look like years | Only 10-K facts with `fp = FY` count as fiscal years (Amazon exposed this) |
| Fiscal years end in different months | Calendar quarter = quarter containing (end − 40 days), the SEC frame convention; fiscal label kept too (`FY2027 Q2`) |
| 10-K/10-Q HTML: TOC and page headers repeat "Item N." | Split on item changes, keep each item's longest span; sub-headings become markdown headings for the chunker |

Output: `facts` (8,673 raw facts), `fin_quarterly` (344 company-quarters), `fin_view` (adds `period_rank`, year-over-year growth), `filings`, and 1,375 filing passages with ticker / form / item metadata.

### Chunking
Heading-aware: a chunk never crosses a heading, keeps its heading path (`NVDA 10-Q (2026-08-26) Item 2 MD&A > Gross Profit and Gross Margin`), splits long sections on paragraph boundaries (180 words for filings, 120 for policies), and drops orphan fragments under 25 words. IDs include a sequence number because filings repeat headings.

### Lineage
Every input file's SHA-256 prefix is recorded in `artifacts/<domain>/lineage.json`; the data version used in cache keys is derived from it, so a data refresh invalidates the cache automatically.

## Results
- NVIDIA FY2027 Q2 gross margin computes to **75.0%** from XBRL, the exact figure printed in its 10-Q MD&A.
- All **48** numeric golden questions agree with the SEC's independently assigned `frame` labels (see [evaluation](../evaluation.md)).
- Adding the finance domain required no domain-specific code in `cortex/` (ADR-017).

## Alternatives considered
- **Use SEC frames directly instead of deriving quarters**: frames are missing for every unreported fiscal Q4, so 8-quarter trends would have gaps. Frames are used as the independent gold instead.
- **Generic HTML-to-text with fixed-size chunks**: loses the Item structure that routes "risk" questions to Risk Factors and "why" questions to MD&A.

## Next
Incremental re-ingest by filing accession; table extraction into structured rows; a third pack (legal, CUAD) to stress long-clause extraction. See [roadmap](../roadmap.md).
