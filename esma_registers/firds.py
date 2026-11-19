"""FIRDS, the register of financial instruments: the reference data of one instrument, by ISIN.

ESMA's public register answers a search for one ISIN with one row per market where the instrument is admitted to
trading (Enel has more than a hundred). The reference data (CFI code, issuer's LEI, currency, a bond's maturity and
rate) are the same on every row; the full name, the market and its dates are the market's own. ``find_instrument``
asks once for every current row and reduces them to one ``FirdsInstrument``, the same whatever the order or the
number of the rows the register sends.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import httpx

from esma_registers import __version__
from esma_registers.errors import InvalidIsin, RateLimited, RegisterUnavailable

#: The public search of the FIRDS register (a Solr collection).
FIRDS_SEARCH_URL = "https://registers.esma.europa.eu/solr/esma_registers_firds/select"
#: The fields read from each row; asking only these keeps the answer small.
FIELDS = (
    "isin",
    "mic",
    "rca_mic",
    "gnr_full_name",
    "gnr_short_name",
    "gnr_cfi_code",
    "lei",
    "gnr_notional_curr_code",
    "gnr_comm_derivative_flag",
    "bnd_maturity_date",
    "bnd_fixed_rate",
    "bnd_nmnl_value_total",
    "bnd_nmnl_value_unit",
    "bnd_nmnl_value_curr_code",
    "bnd_seniority",
    "drv_underlng_isin",
    "mrkt_trdng_start_date",
    "status",
)
#: The most rows asked in one call: far above the busiest instrument seen (Enel, 176 markets). An answer with more
#: rows than this is still reduced, and says so (``rows_read`` < ``rows_found``).
MAX_ROWS = 1000
DEFAULT_TIMEOUT = 20.0
#: A row's ``status``: a cancelled record carries only ``isin`` and ``mic`` and counts for nothing; a terminated one
#: still carries the reference data, on a market where the instrument no longer trades.
CANCELLED = "CANC"
TERMINATED = "TERM"
USER_AGENT = f"esma-registers/{__version__}"
_ISIN = re.compile(r"[A-Z]{2}[A-Z0-9]{9}[0-9]")


def normalize_isin(isin: str) -> str:
    """The ISIN in capitals, checked: twelve characters, a two-letter prefix, nine letters or digits, and the check
    digit of ISO 6166. It is checked before it goes into the register's query."""
    text = isin.strip().upper() if isinstance(isin, str) else ""
    if not _ISIN.fullmatch(text):
        raise InvalidIsin(f"not an ISIN: {isin!r}")
    digits = "".join(str(int(char, 36)) for char in text[:11])
    total = 0
    for position, digit in enumerate(reversed(digits)):
        value = int(digit) * (2 if position % 2 == 0 else 1)
        total += value // 10 + value % 10
    if (10 - total % 10) % 10 != int(text[11]):
        raise InvalidIsin(f"wrong check digit: {isin!r}")
    return text


def query_params(isin: str) -> dict[str, str]:
    """The parameters of the search for one ISIN: every current row (``latest_received_flag:1``), only the fields read,
    no sort (a sort can time the register out)."""
    return {
        "q": f"isin:{normalize_isin(isin)}",
        "fq": "latest_received_flag:1",
        "fl": ",".join(FIELDS),
        "rows": str(MAX_ROWS),
        "wt": "json",
    }


@dataclass(frozen=True, slots=True)
class FirdsInstrument:
    """One instrument as the FIRDS register describes it, its rows reduced to one. Every field is named after the
    register, and is ``None`` when the register does not give it."""

    isin: str
    #: The name of the row of the market the register reports to (``rca_mic``); each market writes its own.
    full_name: str | None
    short_name: str | None
    #: The ISO 10962 classification, six letters (``ESVUFR`` an ordinary share, ``DBFTFB`` a fixed-rate bond…).
    cfi_code: str | None
    issuer_lei: str | None
    #: The currency of the instrument's notional.
    currency: str | None
    commodity_derivative: bool | None
    maturity_date: date | None
    #: The rate the register calls fixed. For some bonds it is the coupon of one period, not the yearly rate (the
    #: Italian BTP Italia and BTP Valore).
    fixed_rate: Decimal | None
    total_issued: Decimal | None
    nominal_unit: Decimal | None
    nominal_currency: str | None
    seniority: str | None
    underlying_isin: str | None
    #: The earliest day the instrument started trading on one of its markets: not an issue date.
    first_trading_date: date | None
    #: The market the register reports to (its MIC).
    reference_market: str | None
    #: The markets where it still trades (MICs: the rows neither terminated nor cancelled), sorted.
    markets: tuple[str, ...]
    #: How many current rows the register has, and how many were read.
    rows_found: int
    rows_read: int


def find_instrument(
    isin: str, *, client: httpx.Client | None = None, timeout: float | None = None
) -> FirdsInstrument | None:
    """The instrument of ``isin`` in the FIRDS register, or ``None`` when the register has no current record of it
    (none at all, or only cancelled ones).

    Raises ``InvalidIsin`` for a text that is not an ISIN, ``RateLimited`` for a refusal for too many requests, and
    ``RegisterUnavailable`` for any other failure. One call, no retry, no wait. ``client`` is used when given, with its own headers and
    timeout; else a client of the library, for this one call, with ``DEFAULT_TIMEOUT``. ``timeout``, when given, applies
    to this call either way."""
    params = query_params(isin)
    if client is not None:
        body = _search(client, params, timeout)
    else:
        with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=DEFAULT_TIMEOUT) as own:
            body = _search(own, params, timeout)
    answer = body["response"]
    rows = [row for row in answer.get("docs") or [] if isinstance(row, dict)]
    found = answer.get("numFound")
    if not isinstance(found, int) or isinstance(found, bool) or (found > 0 and not rows):
        raise RegisterUnavailable("the register's answer contradicts itself (its count and its rows)", status=200)
    usable = [row for row in rows if row.get("status") != CANCELLED]
    if not usable:
        return None
    return _reduce(params["q"].removeprefix("isin:"), usable, found=found, read=len(rows))


def _search(client: httpx.Client, params: dict[str, str], timeout: float | None) -> dict:
    """One call to the register: its JSON body, or the error that says why there is none."""
    try:
        response = client.get(
            FIRDS_SEARCH_URL, params=params, timeout=httpx.USE_CLIENT_DEFAULT if timeout is None else timeout
        )
    except httpx.HTTPError as error:
        raise RegisterUnavailable(f"the register did not answer: {type(error).__name__}") from error
    if response.status_code == 429:
        raise RateLimited(
            "the register refused the call for too many requests",
            retry_after=_seconds(response.headers.get("Retry-After")),
        )
    if response.status_code != 200:
        raise RegisterUnavailable(f"the register answered HTTP {response.status_code}", status=response.status_code)
    try:
        body = response.json()
    except ValueError as error:
        raise RegisterUnavailable("the register's answer is not JSON", status=response.status_code) from error
    header = body.get("responseHeader") if isinstance(body, dict) else None
    if not isinstance(header, dict) or header.get("status") != 0 or not isinstance(body.get("response"), dict):
        raise RegisterUnavailable("the register reported the search as failed", status=response.status_code)
    return body


def _seconds(value: str | None) -> float | None:
    """A ``Retry-After`` in seconds; a date or anything else is no wait the library can read."""
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _value(rows: list[dict], field: str) -> str | None:
    """The value of ``field`` on the rows that have it: the most frequent, a tie to the smaller. Never the row order."""
    counts: dict[str, int] = {}
    for row in rows:
        value = row.get(field)
        if isinstance(value, str) and value.strip():
            counts[value.strip()] = counts.get(value.strip(), 0) + 1
    if not counts:
        return None
    return min(counts, key=lambda value: (-counts[value], value))


def _day(text: str | None) -> date | None:
    """A date of the register (``2032-06-04T00:00:00Z``); ``None`` for one it writes badly, never an error."""
    try:
        return date.fromisoformat(text[:10]) if text else None
    except ValueError:
        return None


def _decimal(text: str | None) -> Decimal | None:
    """An amount or a rate of the register; ``None`` for one it writes badly, never an error."""
    try:
        value = Decimal(text) if text else None
    except ArithmeticError:
        return None
    return value if value is None or value.is_finite() else None


def _flag(text: str | None) -> bool | None:
    return {"yes": True, "no": False}.get(text.lower()) if text else None


def _reduce(isin: str, rows: list[dict], *, found: int, read: int) -> FirdsInstrument:
    """The rows of one ISIN as one instrument (see the module's docstring); ``rows`` without the cancelled ones."""
    reference = _value(rows, "rca_mic")
    at_reference = [row for row in rows if reference is not None and row.get("mic") == reference]
    starts = [day for row in rows if (day := _day(row.get("mrkt_trdng_start_date"))) is not None]
    return FirdsInstrument(
        isin=isin,
        full_name=_value(at_reference, "gnr_full_name") or _value(rows, "gnr_full_name"),
        short_name=_value(rows, "gnr_short_name"),
        cfi_code=_value(rows, "gnr_cfi_code"),
        issuer_lei=_value(rows, "lei"),
        currency=_value(rows, "gnr_notional_curr_code"),
        commodity_derivative=_flag(_value(rows, "gnr_comm_derivative_flag")),
        maturity_date=_day(_value(rows, "bnd_maturity_date")),
        fixed_rate=_decimal(_value(rows, "bnd_fixed_rate")),
        total_issued=_decimal(_value(rows, "bnd_nmnl_value_total")),
        nominal_unit=_decimal(_value(rows, "bnd_nmnl_value_unit")),
        nominal_currency=_value(rows, "bnd_nmnl_value_curr_code"),
        seniority=_value(rows, "bnd_seniority"),
        underlying_isin=_value(rows, "drv_underlng_isin"),
        first_trading_date=min(starts) if starts else None,
        reference_market=reference,
        markets=tuple(
            sorted(
                {
                    row["mic"]
                    for row in rows
                    if isinstance(row.get("mic"), str) and row["mic"] and row.get("status") != TERMINATED
                }
            )
        ),
        rows_found=max(found, read),
        rows_read=read,
    )
