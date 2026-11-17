"""Download real SEC data for the finance domain. Standard library only, so it runs anywhere.

    python -m cortex.connectors.sec_fetch                  # default 8 companies
    python -m cortex.connectors.sec_fetch NVDA AMD INTC    # your own tickers

Uses the SEC's free public APIs (no key). The SEC asks every client to send a
User-Agent with a contact email and to stay under 10 requests per second.

Writes, per ticker, into data/raw/sec/<TICKER>/:
  companyfacts.json   every XBRL fact the company has reported (revenue, margins, ...)
  submissions.json    filing index
  10-K_<date>.htm     the latest annual report (full document)
  10-Q_<date>.htm     the latest quarterly report
"""
from __future__ import annotations

import gzip
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

UA = os.environ.get("SEC_USER_AGENT", "")      # "Your Name your.email@example.com" (SEC fair-access policy)
DEFAULT = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN"]   # matches domains/finance/glossary.yaml
KNOWN_CIK = {"AAPL": "0000320193", "MSFT": "0000789019", "NVDA": "0001045810", "GOOGL": "0001652044",
             "AMZN": "0001018724", "META": "0001326801", "TSLA": "0001318605", "JPM": "0000019617"}
ROOT = Path(__file__).resolve().parents[2]
OUT = Path(os.environ.get("CORTEX_DATA_DIR", ROOT / "data")) / "raw" / "sec"


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                time.sleep(0.15)                       # stay well under 10 req/s
                return body
        except Exception as e:  # noqa: BLE001
            if attempt == 3:
                raise
            print(f"  retry {attempt + 1} for {url}: {e}")
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("unreachable")


def main(tickers: list[str]) -> None:
    global UA
    if not UA or "@" not in UA:
        UA = input("SEC requires a contact. Enter 'Your Name your@email.com': ").strip()
    if "@" not in UA:
        sys.exit("A real name and email are required by the SEC (https://www.sec.gov/os/accessing-edgar-data).")
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"Writing to {OUT}")
    cik_of = dict(KNOWN_CIK)
    if any(t.upper() not in cik_of for t in tickers):
        index = json.loads(get("https://www.sec.gov/files/company_tickers.json"))
        cik_of.update({v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in index.values()})
    manifest = {}
    for t in tickers:
        t = t.upper()
        if t not in cik_of:
            print(f"{t}: not found in SEC ticker list, skipped")
            continue
        cik = cik_of[t]
        d = OUT / t
        d.mkdir(exist_ok=True)
        print(f"{t} (CIK {cik})")
        (d / "companyfacts.json").write_bytes(get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"))
        subs_raw = get(f"https://data.sec.gov/submissions/CIK{cik}.json")
        (d / "submissions.json").write_bytes(subs_raw)
        recent = json.loads(subs_raw)["filings"]["recent"]
        files = []
        for form in ("10-K", "10-Q"):
            for i, f in enumerate(recent["form"]):
                if f == form:
                    acc = recent["accessionNumber"][i].replace("-", "")
                    doc = recent["primaryDocument"][i]
                    date = recent["filingDate"][i]
                    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{doc}"
                    name = f"{form}_{date}.htm"
                    (d / name).write_bytes(get(url))
                    files.append({"form": form, "filed": date, "url": url, "file": name,
                                  "period": recent["reportDate"][i]})
                    print(f"  {form} filed {date}")
                    break
        manifest[t] = {"cik": cik, "name": json.loads(subs_raw).get("name"), "filings": files}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Done: {len(manifest)} companies. Manifest at {OUT / 'manifest.json'}")


if __name__ == "__main__":
    main(sys.argv[1:] or DEFAULT)
