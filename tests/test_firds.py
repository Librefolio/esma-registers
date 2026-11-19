"""find_instrument: the FIRDS search of one ISIN, reduced to one instrument.

Pinned:
- the query: every current row (``latest_received_flag:1``), only the fields read, no sort, the ISIN checked and in
  capitals, the library's User-Agent;
- the reduction: a field the rows agree on is that value; rows that lack a field do not count; a cancelled row
  (``status`` ``CANC``, it carries only ``isin`` and ``mic``) counts for nothing, and an ISIN with only cancelled rows
  is ``None``; the markets are those where it still trades (neither ``TERM`` nor ``CANC``); on a disagreement the most frequent value wins, a tie the smaller one; the full name is the one
  of the row of the market the register reports to; the markets sorted; the first trading day the earliest. The same
  result whatever the order of the rows;
- the values typed: dates as ``date``, amounts and rates as ``Decimal``, the commodity flag as ``bool``;
- the outcomes: an ISIN the register does not have is ``None``; a text that is not an ISIN is ``InvalidIsin`` and asks
  nothing; HTTP 429 is ``RateLimited`` with its ``Retry-After``; any other failure ``RegisterUnavailable``. No retry;
- an answer with more rows than asked is reduced and says so (``rows_read`` < ``rows_found``).

How: the register's answers recorded with ``tools/record_fixtures.py`` (``tests/fixtures/``, the body whole), replayed
through ``httpx.MockTransport``; no test reaches the network (the one ``integration`` test is excluded by default).
The synthetic answers, for the shapes the recordings lack, are built from recorded rows and say so.
"""

from __future__ import annotations

import json
import random
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

import esma_registers
from esma_registers import FirdsInstrument, InvalidIsin, RateLimited, RegisterUnavailable, find_instrument
from esma_registers.firds import FIELDS, FIRDS_SEARCH_URL, MAX_ROWS

FIXTURES = Path(__file__).parent / "fixtures"


def recorded(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def with_docs(body: dict, docs: list[dict], found: int | None = None) -> dict:
    """A copy of a recorded answer with other rows (synthetic)."""
    copy = json.loads(json.dumps(body))
    copy["response"]["docs"] = docs
    copy["response"]["numFound"] = len(docs) if found is None else found
    return copy


class Register:
    """A fake register: answers every request with ``body`` (or ``response``), and keeps the requests."""

    def __init__(
        self, body: dict | None = None, *, response: httpx.Response | None = None, error: Exception | None = None
    ) -> None:
        self.body, self.response, self.error = body, response, error
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.response if self.response is not None else httpx.Response(200, json=self.body)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def find(name_or_body: str | dict, isin: str) -> FirdsInstrument | None:
    register = Register(recorded(name_or_body) if isinstance(name_or_body, str) else name_or_body)
    with register.client() as client:
        return find_instrument(isin, client=client)


def rows_of(name: str) -> list[dict]:
    return recorded(name)["response"]["docs"]


# =============================================================================
# The query
# =============================================================================


def test_asks_every_current_row_of_the_isin_with_only_the_fields_read() -> None:
    register = Register(recorded("enel"))
    with register.client() as client:
        find_instrument(" it0003128367 ", client=client)

    [request] = register.requests
    assert str(request.url).startswith(FIRDS_SEARCH_URL)
    params = dict(request.url.params)
    assert params["q"] == "isin:IT0003128367", "the ISIN checked and in capitals"
    assert params["fq"] == "latest_received_flag:1", "only the current rows"
    assert params["fl"].split(",") == list(FIELDS)
    assert params["rows"] == str(MAX_ROWS)
    assert params["wt"] == "json"
    assert "sort" not in params, "a sort can time the register out"


def test_a_text_that_is_not_an_isin_asks_nothing() -> None:
    register = Register(recorded("enel"))
    with register.client() as client:
        for text in ["", "IT000312836", "IT0003128368", "IT00031283&7", "1T0003128367"]:
            with pytest.raises(InvalidIsin):
                find_instrument(text, client=client)
    assert register.requests == []


def test_the_timeout_is_the_clients_unless_one_is_given() -> None:
    register = Register(recorded("enel"))
    with httpx.Client(transport=httpx.MockTransport(register), timeout=10.0) as client:
        find_instrument("IT0003128367", client=client)
        find_instrument("IT0003128367", client=client, timeout=3.0)
    assert [request.extensions["timeout"]["read"] for request in register.requests] == [10.0, 3.0]


def test_without_a_client_the_library_names_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    register = Register(recorded("enel"))
    made: list[dict] = []
    real_client = httpx.Client

    def client_factory(**kwargs: object) -> httpx.Client:
        made.append(kwargs)
        return real_client(transport=httpx.MockTransport(register), **kwargs)

    monkeypatch.setattr(httpx, "Client", client_factory)
    assert find_instrument("IT0003128367") is not None
    assert made and made[0]["headers"]["User-Agent"] == f"esma-registers/{esma_registers.__version__}"
    assert register.requests[0].extensions["timeout"]["read"] == 20.0, "the library's own client: 20 s"
    assert register.requests[0].headers["User-Agent"] == f"esma-registers/{esma_registers.__version__}"


# =============================================================================
# The reduction, on recorded answers
# =============================================================================


def test_a_share_on_many_markets() -> None:
    instrument = find("enel", "IT0003128367")
    rows = rows_of("enel")

    assert instrument is not None
    assert instrument.isin == "IT0003128367"
    assert (instrument.cfi_code, instrument.issuer_lei, instrument.currency) == (
        "ESVUFR",
        "WOCMU6HCI0OJWNPRZS33",
        "EUR",
    )
    reference = [row for row in rows if row.get("mic") == row.get("rca_mic")]
    assert instrument.reference_market == reference[0]["mic"]
    assert instrument.full_name == reference[0]["gnr_full_name"], "the name of the market the register reports to"
    trading = {row["mic"] for row in rows if row.get("status") not in ("TERM", "CANC")}
    assert instrument.markets == tuple(sorted(trading)), "the markets where it still trades"
    assert len(trading) < len({row["mic"] for row in rows}), "barrier: some current rows are terminated or cancelled"
    starts = [
        row["mrkt_trdng_start_date"] for row in rows if row.get("mrkt_trdng_start_date") and row.get("status") != "CANC"
    ]
    assert instrument.first_trading_date == min(date.fromisoformat(start[:10]) for start in starts)
    assert (instrument.rows_found, instrument.rows_read) == (len(rows), len(rows))
    assert (instrument.maturity_date, instrument.fixed_rate, instrument.total_issued) == (None, None, None)
    assert instrument.commodity_derivative is False


def test_a_state_bond_whose_fixed_rate_is_the_period_coupon() -> None:
    instrument = find("btp_italia", "IT0005648255")

    assert instrument is not None
    assert instrument.cfi_code == "DBVTFB"
    assert instrument.maturity_date == date(2032, 6, 4)
    assert instrument.fixed_rate == Decimal("0.925"), "the register's value, as it is: the coupon of one period"
    assert instrument.total_issued == Decimal(2257423000)
    assert (instrument.nominal_unit, instrument.nominal_currency) == (Decimal(1000), "EUR")
    assert instrument.issuer_lei == "815600DE60799F5A9309"


@pytest.mark.parametrize(
    ("name", "isin", "cfi", "maturity", "rate", "issued"),
    [
        ("btp_2072", "IT0005441883", "DBFTFB", date(2072, 3, 1), Decimal("2.15"), Decimal(9400000000)),
        ("bond_corp", "XS2331921390", "DBFNGB", date(2028, 4, 22), Decimal("1.5"), Decimal(300000000)),
    ],
)
def test_fixed_rate_bonds(name: str, isin: str, cfi: str, maturity: date, rate: Decimal, issued: Decimal) -> None:
    instrument = find(name, isin)

    assert instrument is not None
    assert (instrument.cfi_code, instrument.maturity_date, instrument.fixed_rate, instrument.total_issued) == (
        cfi,
        maturity,
        rate,
        issued,
    )


def test_an_etf_and_an_etc() -> None:
    etf = find("swda", "IE00B4L5Y983")
    etc = find("etc_gold", "IE00B4ND3602")

    assert etf is not None and etc is not None
    assert etf.cfi_code is not None and etf.cfi_code.startswith("C"), "a collective investment vehicle"
    assert etf.underlying_isin is None
    assert etc.underlying_isin is not None and len(etc.underlying_isin) == 12
    assert isinstance(etc.commodity_derivative, bool)


def test_an_isin_the_register_does_not_have_is_none() -> None:
    assert find("not_found_fund", "IT0005487001") is None


def test_the_order_of_the_rows_never_changes_the_result() -> None:
    body = recorded("enel")
    first = find(body, "IT0003128367")
    shuffled = body["response"]["docs"][:]
    random.Random(68).shuffle(shuffled)

    assert find(with_docs(body, shuffled), "IT0003128367") == first
    assert find(with_docs(body, list(reversed(shuffled))), "IT0003128367") == first


# =============================================================================
# The reduction, on synthetic answers (built from recorded rows)
# =============================================================================


def test_a_disagreement_goes_to_the_most_frequent_value_and_a_tie_to_the_smaller() -> None:
    body = recorded("btp_italia")
    [one, two, three] = [dict(row) for row in body["response"]["docs"][:3]]
    one["lei"], two["lei"], three["lei"] = "BBBBBBBBBBBBBBBBBB00", "BBBBBBBBBBBBBBBBBB00", "AAAAAAAAAAAAAAAAAA00"
    assert find(with_docs(body, [one, two, three]), "IT0005648255").issuer_lei == "BBBBBBBBBBBBBBBBBB00"
    two["lei"] = "AAAAAAAAAAAAAAAAAA00"
    three.pop("lei")
    assert (
        find(with_docs(body, [one, two, three]), "IT0005648255").issuer_lei == "AAAAAAAAAAAAAAAAAA00"
    ), "a tie: the smaller; a row without the field does not count"


def test_without_a_row_of_the_reference_market_the_most_frequent_name() -> None:
    body = recorded("btp_italia")
    rows = [dict(row) for row in body["response"]["docs"] if row.get("mic") != row.get("rca_mic")][:3]
    rows[0]["gnr_full_name"], rows[1]["gnr_full_name"], rows[2]["gnr_full_name"] = "NAME B", "NAME A", "NAME B"
    assert find(with_docs(body, rows), "IT0005648255").full_name == "NAME B"


def test_a_value_the_register_writes_badly_is_none_never_an_error() -> None:
    body = recorded("btp_italia")
    rows = [dict(row) for row in body["response"]["docs"]]
    for row in rows:
        row["bnd_fixed_rate"], row["bnd_maturity_date"], row["bnd_nmnl_value_total"] = "n/a", "soon", "1,000"
    instrument = find(with_docs(body, rows), "IT0005648255")

    assert (instrument.fixed_rate, instrument.maturity_date, instrument.total_issued) == (None, None, None)
    assert instrument.cfi_code == "DBVTFB", "the other fields are read"


def test_cancelled_rows_count_for_nothing() -> None:
    body = recorded("btp_italia")
    rows = [dict(row) for row in body["response"]["docs"]]
    cancelled = dict(rows[0], status="CANC", lei="ZZZZZZZZZZZZZZZZZZ00", mic="ZZZZ")

    instrument = find(with_docs(body, rows + [dict(cancelled) for _ in range(30)]), "IT0005648255")
    assert instrument.issuer_lei == "815600DE60799F5A9309", "the cancelled rows' values never win"
    assert "ZZZZ" not in instrument.markets
    assert find(with_docs(body, [cancelled]), "IT0005648255") is None, "only cancelled rows: nothing to read"


def test_an_answer_with_more_rows_than_read_says_so() -> None:
    body = recorded("btp_italia")
    rows = body["response"]["docs"][:2]
    instrument = find(with_docs(body, rows, found=500), "IT0005648255")

    assert (instrument.rows_found, instrument.rows_read) == (500, 2)


# =============================================================================
# Failures
# =============================================================================


def outcome(register: Register) -> Exception:
    with register.client() as client, pytest.raises(RegisterUnavailable) as caught:
        find_instrument("IT0003128367", client=client)
    assert len(register.requests) == 1, "one call, no retry"
    return caught.value


def test_too_many_requests_is_rate_limited_with_the_wait_asked() -> None:
    error = outcome(Register(response=httpx.Response(429, headers={"Retry-After": "7"})))
    assert isinstance(error, RateLimited) and (error.status, error.retry_after) == (429, 7.0)

    error = outcome(Register(response=httpx.Response(429)))
    assert isinstance(error, RateLimited) and error.retry_after is None


@pytest.mark.parametrize(
    "response",
    [{"numFound": 3, "docs": []}, {"numFound": "many", "docs": []}],
    ids=["rows announced, none sent", "a count that is not a number"],
)
def test_an_answer_that_contradicts_itself_is_register_unavailable_not_none(response: dict) -> None:
    error = outcome(Register({"responseHeader": {"status": 0}, "response": response}))
    assert not isinstance(error, RateLimited)


@pytest.mark.parametrize(
    ("register", "status"),
    [
        (Register(response=httpx.Response(503, text="Service Unavailable")), 503),
        (Register(response=httpx.Response(504, text="Gateway Time-out")), 504),
        (Register(response=httpx.Response(200, text="<html>not json</html>")), 200),
        (Register({"responseHeader": {"status": 400}, "error": {"msg": "bad query"}}), 200),
        (Register({"responseHeader": {"status": 0}}), 200),
        (Register(error=httpx.ConnectError("refused")), None),
        (Register(error=httpx.ReadTimeout("slow")), None),
    ],
    ids=["503", "504", "not JSON", "solr error", "no response", "connection", "timeout"],
)
def test_any_other_failure_is_register_unavailable(register: Register, status: int | None) -> None:
    error = outcome(register)
    assert not isinstance(error, RateLimited)
    assert error.status == status


# =============================================================================
# The real register (excluded by default: `pytest -m integration`)
# =============================================================================


@pytest.mark.integration
def test_the_real_register_knows_enel() -> None:
    instrument = find_instrument("IT0003128367")
    assert instrument is not None and instrument.cfi_code == "ESVUFR"
