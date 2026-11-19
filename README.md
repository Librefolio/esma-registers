# 🇪🇺 esma-registers

A small Python library to read the public registers of the European Securities and Markets Authority (ESMA).

Today it reads one: **FIRDS**, the register of financial instruments, which gives an instrument's reference data by ISIN —
its ISO 10962 classification (CFI code), its issuer's LEI, its currency and, for a bond, its maturity, rate and amount
issued. One call per ISIN, a JSON answer, no key.

## ✨ What it does

- 🔎 **One instrument by ISIN** — `find_instrument("IT0003128367")`.
- 🧮 **One answer per ISIN** — the register sends one row per market where the instrument trades (a share can have more
  than a hundred). The library asks only the current rows and reduces them to one instrument. The result is the same
  whatever the order or the number of the rows.
- 🧾 **Typed values** — dates as `date`, amounts and rates as `Decimal`, flags as `bool`, every field named after the
  register.
- 🚦 **Clear outcomes** — an ISIN the register does not have is `None`. A refusal for too many requests is
  `RateLimited`, with the wait the register asked for. Any other failure is `RegisterUnavailable`.

## 📦 Installation

> The repository's address is to be confirmed when it is published.

```bash
pip install git+https://github.com/Librefolio/esma-registers.git
```

In a `Pipfile`:

```toml
[packages]
esma-registers = {git = "https://github.com/Librefolio/esma-registers.git", ref = "main"}
```

## 🚀 Quickstart

```python
from esma_registers import RateLimited, RegisterUnavailable, find_instrument

instrument = find_instrument("IT0005648255")
if instrument is None:
    print("not in the register")          # an investment fund that is not traded on a market, for example
else:
    print(instrument.cfi_code)            # DBVTFB: a bond, variable rate, guaranteed by the State
    print(instrument.issuer_lei)          # 815600DE60799F5A9309
    print(instrument.maturity_date)       # 2032-06-04
    print(instrument.markets)             # every market where it trades (MICs), sorted
```

Pass your own `httpx.Client` to reuse connections or set your own headers:

```python
import httpx

with httpx.Client(timeout=10) as client:
    instrument = find_instrument("IT0003128367", client=client)
```

## 📖 API

### `find_instrument(isin, *, client=None, timeout=None) -> FirdsInstrument | None`

It checks the ISIN (format and check digit) before asking, makes **one** call to the register, and never retries or
waits by itself: pacing your calls is up to you. With your own `client`, its timeout applies; without, the library's
client waits 20 seconds. A `timeout` given here applies to this call either way.

| Outcome | What it means |
|---|---|
| a `FirdsInstrument` | the register has the instrument |
| `None` | the register has no current record of it: none at all (a fund that is not traded on a market, for example), or only cancelled ones |
| `InvalidIsin` | the text is not an ISIN; nothing was asked |
| `RateLimited` | HTTP 429; `retry_after` holds the seconds the register asked to wait, when it said |
| `RegisterUnavailable` | a network error or a timeout, an HTTP error, an answer that is not the register's JSON, or a search the register reported as failed; `status` holds the HTTP status when there was one |

All the errors derive from `EsmaRegistersError`.

### `FirdsInstrument`

| Field | Register field | Notes |
|---|---|---|
| `isin` | `isin` | in capitals |
| `full_name` | `gnr_full_name` | each market writes its own name: this one comes from the market the register reports to |
| `short_name` | `gnr_short_name` | |
| `cfi_code` | `gnr_cfi_code` | ISO 10962, six letters (`ESVUFR` an ordinary share, `DBFTFB` a fixed-rate state bond…) |
| `issuer_lei` | `lei` | the issuer's Legal Entity Identifier |
| `currency` | `gnr_notional_curr_code` | |
| `commodity_derivative` | `gnr_comm_derivative_flag` | |
| `maturity_date` | `bnd_maturity_date` | bonds |
| `fixed_rate` | `bnd_fixed_rate` | bonds. The value as the register gives it: for some bonds it is the coupon **of one period**, not the yearly rate (Italy's BTP Italia and BTP Valore) |
| `total_issued` | `bnd_nmnl_value_total` | bonds |
| `nominal_unit`, `nominal_currency` | `bnd_nmnl_value_unit`, `bnd_nmnl_value_curr_code` | bonds |
| `seniority` | `bnd_seniority` | bonds |
| `underlying_isin` | `drv_underlng_isin` | for example an ETC's underlying |
| `first_trading_date` | `mrkt_trdng_start_date` | the earliest day it started trading on one of its markets: **not** an issue date |
| `reference_market` | `rca_mic` | the market the register reports to (a MIC) |
| `markets` | `mic` | the markets where it still trades, sorted: a market whose record is terminated or cancelled is left out |
| `rows_found`, `rows_read` | | how many current rows the register has, and how many were read |

A field the register does not give is `None`.

## ⚠️ Technical notes

- **The query.** `q=isin:<ISIN>`, `fq=latest_received_flag:1` (only the current rows: without it older records come
  too), `fl=` the fields read, `rows=1000`, **no sort** (a sort can time the register out).
- **The current rows.** Every row the filter drops is an older record of a market that also has a current one. Among
  the current rows, a cancelled record (`status` `CANC`) carries only `isin` and `mic` and counts for nothing; a
  terminated one (`TERM`, a market where the instrument no longer trades) still carries the reference data.
- **The reduction.** A field the rows agree on is that value, and rows that lack a field do not count. On a
  disagreement the most frequent value wins, and a tie goes to the smaller value.
- **An answer with more rows than asked** is reduced from the rows read, and says so (`rows_read < rows_found`).

## 🏛️ Data source and terms

The data come from ESMA's public FIRDS register (`registers.esma.europa.eu`), through the search service of the register's
website. **This library is not affiliated with or endorsed by ESMA.**

ESMA's legal notice allows reproducing the information on its website, provided the source is acknowledged. It says
nothing specific about the registers or about automated access. Use the library with moderation, cite ESMA as the source,
and check the terms yourself before relying on it.

### References consulted

These projects were read to understand the register. **No code was copied from them**, and they are not dependencies:

- `esma_data_py`, by ESMA (EUPL-1.2): the register's addresses and the CFI letters; it reads the whole files, without
  a search by ISIN.
- `pyfirds` (MIT): how the whole FIRDS files are read.
- `mcp-esma-firds`, by pipeworx-io (MIT, JavaScript): the search by ISIN and by issuer, and its notes on querying the
  register.

## 🧪 Tests

The tests replay answers recorded from the register (`tests/fixtures/`, each body whole): they never reach the
network.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest                      # the recorded answers
.venv/bin/pytest -m integration       # one call to the real register
.venv/bin/ruff check . && .venv/bin/black --check .
```

`tools/record_fixtures.py` records the answers again, with the library's own query. Run it sparingly: it calls the real
register.

## 📂 Structure

```
esma_registers/
├── __init__.py      # the public API
├── errors.py        # EsmaRegistersError, InvalidIsin, RegisterUnavailable, RateLimited
└── firds.py         # find_instrument, FirdsInstrument, the query and the reduction
tests/
├── fixtures/        # recorded answers of the register
└── test_firds.py
tools/
└── record_fixtures.py
```

## 📝 License

LGPL-3.0-or-later: see [LICENSE](LICENSE) and [LICENSE.GPL-3.0](LICENSE.GPL-3.0).

You may use this library as a dependency in any project, closed-source included. Changes to **the library itself** must
stay open.
