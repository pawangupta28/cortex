"""Finance pack tests. Pure functions always run; the end-to-end test needs SEC data
(python -m cortex.cli --domain finance fetch-sec) and is skipped without it."""
import importlib.util
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SEC = ROOT / "data" / "raw" / "sec"
spec = importlib.util.spec_from_file_location("finance_pack", ROOT / "domains" / "finance" / "pack.py")
pack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pack)


def _fact(start, end, val, form="10-Q", fp="Q1", filed="2026-01-01"):
    return {"start": start, "end": end, "val": val, "form": form, "fp": fp, "filed": filed, "accn": "x"}


def test_calendar_alignment_across_fiscal_years():
    assert pack.cal_quarter(date(2026, 7, 26)) == (2026, 2)      # NVIDIA fiscal Q2 FY2027
    assert pack.cal_quarter(date(2025, 12, 27)) == (2025, 4)     # Apple fiscal Q1 FY2026
    assert pack.cal_quarter(date(2026, 6, 30)) == (2026, 2)      # Microsoft fiscal Q4 FY2026


def test_q4_is_derived_from_annual_minus_three_quarters():
    rev = [_fact("2025-01-01", "2025-03-31", 10), _fact("2025-04-01", "2025-06-30", 20),
           _fact("2025-07-01", "2025-09-30", 30),
           _fact("2025-01-01", "2025-12-31", 100, form="10-K", fp="FY")]
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": rev}}}}}
    rows = pack.quarterly_rows("TST", "Test", facts)
    q4 = [r for r in rows if r["period_end"] == "2025-12-31"][0]
    assert q4["revenue"] == 40 and q4["q4_derived"] and q4["fiscal_label"] == "FY2025 Q4"


def test_latest_filing_wins_and_ttm_is_not_a_fiscal_year():
    rev = [_fact("2025-01-01", "2025-03-31", 10, filed="2025-05-01"),
           _fact("2025-01-01", "2025-03-31", 11, filed="2026-05-01"),        # restated later
           _fact("2024-04-01", "2025-03-31", 999, form="10-Q", fp="Q1")]      # trailing twelve months
    rows = pack.quarterly_rows("TST", "Test", {"facts": {"us-gaap": {"Revenues": {"units": {"USD": rev}}}}})
    assert [r["revenue"] for r in rows] == [11]


def test_split_items_prefers_body_over_table_of_contents():
    text = "Item 1A. Risk Factors\n2\nItem 7. MD&A\n5\n\nItem 1A. Risk Factors\n" + "risk text. " * 300 + \
           "\nItem 7. Management's Discussion\n" + "mdna text. " * 300
    items = pack.split_items(text)
    assert "risk text" in items["1a"] and "mdna text" in items["7"]


@pytest.mark.skipif(not (SEC / "NVDA" / "companyfacts.json").exists(), reason="no SEC data downloaded")
def test_real_nvidia_quarter_matches_its_10q():
    facts = json.loads((SEC / "NVDA" / "companyfacts.json").read_text(encoding="utf-8"))
    rows = {r["period_end"]: r for r in pack.quarterly_rows("NVDA", "NVIDIA", facts)}
    r = rows.get("2026-07-26")
    if r is None:
        pytest.skip("data snapshot differs")
    assert round(r["gross_profit"] / r["revenue"], 3) == 0.75      # 10-Q states 75.0%


@pytest.mark.skipif(not (SEC / "NVDA" / "companyfacts.json").exists(), reason="no SEC data downloaded")
def test_finance_end_to_end(tmp_path):
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "raw" / "sec").symlink_to(SEC, target_is_directory=True)
    env = {**os.environ, "CORTEX_DATA_DIR": str(tmp_path / "data"), "CORTEX_ARTIFACTS_DIR": str(tmp_path / "art"),
           "CORTEX_DOMAIN": "finance"}
    run = lambda *a: subprocess.run([sys.executable, "-m", "cortex.cli", *a], cwd=ROOT, env=env,  # noqa: E731
                                    capture_output=True, text=True, timeout=600)
    assert run("init").returncode == 0
    out = run("ask", "Which company had the highest operating margin in the latest quarter?", "--json")
    ans = json.loads(out.stdout)
    assert not ans["abstained"] and all(c["verified"] for c in ans["claims"])
    out = run("ask", "What was Tesla's revenue last quarter?", "--json")
    assert json.loads(out.stdout)["abstained"]
    scoped = json.loads(run("ask", "revenue by company", "--json", "--role", "semis_analyst").stdout)
    rows = next(e["data"]["rows"] for e in scoped["evidence"] if e["kind"] == "sql_result")
    assert {r[0] for r in rows} == {"NVDA"}
