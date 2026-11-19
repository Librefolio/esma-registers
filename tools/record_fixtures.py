"""Record the register's answers the tests replay: the same query as the library, the body as it came.

    .venv/bin/python tools/record_fixtures.py

It calls the real register, one ISIN at a time with a pause between calls, and writes ``tests/fixtures/<name>.json``
(the Solr body, whole) and prints the time of each call. The instruments are listed securities whose data the
register publishes; re-recording changes the fixtures, so the tests' expectations may need updating with them.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esma_registers.firds import FIRDS_SEARCH_URL, USER_AGENT, query_params

#: Name → ISIN: a share on many markets, bonds (state fixed and floating-like, corporate), ETFs, an ETC, and an ISIN of
#: a fund the register does not have.
INSTRUMENTS = {
    "enel": "IT0003128367",
    "btp_italia": "IT0005648255",
    "btp_2072": "IT0005441883",
    "bond_corp": "XS2331921390",
    "swda": "IE00B4L5Y983",
    "etc_gold": "IE00B4ND3602",
    "not_found_fund": "IT0005487001",
}
PAUSE_SECONDS = 2.0


def main() -> None:
    out = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
    out.mkdir(parents=True, exist_ok=True)
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0) as client:
        for index, (name, isin) in enumerate(INSTRUMENTS.items()):
            if index:
                time.sleep(PAUSE_SECONDS)
            started = time.monotonic()
            response = client.get(FIRDS_SEARCH_URL, params=query_params(isin))
            seconds = time.monotonic() - started
            response.raise_for_status()
            body = response.json()
            (out / f"{name}.json").write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
            found = body.get("response", {}).get("numFound")
            read = len(body.get("response", {}).get("docs", []))
            print(
                f"{name:16} {isin}  found {found:>4}  read {read:>4}  {seconds:5.2f} s  {len(response.content):>7} bytes"
            )


if __name__ == "__main__":
    main()
