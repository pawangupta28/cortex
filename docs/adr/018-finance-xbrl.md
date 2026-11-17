# ADR-018: Finance data — concept ranking, derived Q4, calendar alignment, independent gold

Status: accepted · 2026-11-17

## Decisions
1. **Concept resolution by recency.** Per metric, candidate XBRL concepts are ranked by their latest reported period; each period takes the first concept that reports it. Fixes NVIDIA (`Revenues`) vs Apple (`RevenueFromContract…`) and mid-history concept switches.
2. **Latest filing wins** for restated periods.
3. **Only 10-K fiscal-year facts are years.** 10-Qs carry trailing-twelve-month figures, which otherwise get mistaken for fiscal years and corrupt Q4 derivation (Amazon showed this).
4. **Fiscal Q4 = FY − (Q1+Q2+Q3)** for additive metrics; flagged; EPS not derived.
5. **Calendar quarter = quarter containing (end − 40 days)**, the SEC frame convention, so companies with different fiscal years compare correctly.
6. **Gold answers come from SEC `frame` labels**, an alignment independent of decision 5.

## Evidence
All 48 numeric golden questions (values, rankings, 8-quarter trends) agree with SEC frames. NVIDIA FY2027 Q2 gross margin computes to 75.0%, which matches the figure printed in its 10-Q.
