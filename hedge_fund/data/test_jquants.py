"""J-Quants V2 response mapping and failure behavior, without a live key."""

import requests
import pytest

from hedge_fund.data.jquants import JQuantsDataError, JQuantsPriceClient


def bar(day, *, code="72030", close=2000, factor=1.0, **extra):
    return dict(Date=day, Code=code, O=close - 10, H=close + 10,
                L=close - 20, C=close, Vo=1000, AdjFactor=factor,
                AdjC=close / factor, **extra)


class Response:
    def __init__(self, data, status=200):
        self.data = data
        self.status = status

    def raise_for_status(self):
        if self.status != 200:
            raise requests.HTTPError(f"HTTP {self.status}")

    def json(self):
        return self.data


class Session:
    def __init__(self, *pages):
        self.pages = list(pages)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.pages.pop(0)

    def close(self):
        pass


def client(*pages):
    session = Session(*pages)
    return JQuantsPriceClient(api_key="test-key", session=session,
                             requests_per_minute=1_000_000), session


def test_pages_unadjusted_ohlcv_and_preload(monkeypatch):
    monkeypatch.setattr("hedge_fund.data.jquants.time.sleep", lambda _: None)
    no_trade = bar("2025-01-08")
    no_trade.update(O=None, H=None, L=None, C=None, Vo=None)
    data, session = client(
        Response({"data": [bar("2025-01-06", close=2000), no_trade],
                  "pagination_key": "next"}),
        Response({"data": [bar("2025-01-07", close=2100)],
                  "pagination_key": None}),
    )
    data.preload_prices("7203.T", "2025-01-01", "2025-01-31")
    prices = data.get_prices("7203", "2025-01-07", "2025-01-07")
    assert [p.close for p in prices] == [2100]
    assert len(session.calls) == 2
    url, kwargs = session.calls[0]
    assert url.endswith("/v2/equities/bars/daily")
    assert kwargs["params"] == {"code": "7203", "from": "2025-01-01", "to": "2025-01-31"}
    assert kwargs["headers"] == {"x-api-key": "test-key"}
    assert session.calls[1][1]["params"]["pagination_key"] == "next"


def test_split_bar_stops_instead_of_corrupting_positions():
    data, _ = client(Response({"data": [bar("2025-01-06", factor=0.5)]}))
    with pytest.raises(JQuantsDataError, match="split/consolidation"):
        data.get_prices("7203", "2025-01-01", "2025-01-31")


def test_api_failure_stops_instead_of_becoming_empty_history():
    data, _ = client(Response({}, status=500))
    with pytest.raises(JQuantsDataError, match="request failed"):
        data.get_prices("7203", "2025-01-01", "2025-01-31")


def test_rejects_non_jpx_ticker_and_intraday():
    data, _ = client()
    with pytest.raises(ValueError, match="JPX code"):
        data.get_prices("AAPL", "2025-01-01", "2025-01-31")
    with pytest.raises(ValueError, match="daily"):
        data.get_prices("7203", "2025-01-01", "2025-01-31", interval="hour")


class ErrorResponse(Response):
    status_code = 400

    def raise_for_status(self):
        raise requests.HTTPError("HTTP 400", response=self)


def test_api_error_reports_status_and_message_without_key():
    data, _ = client(ErrorResponse({"message": "outside subscription period"}))
    with pytest.raises(JQuantsDataError) as err:
        data.get_prices("7203", "2025-01-01", "2025-01-31")
    assert "HTTP 400 outside subscription period" in str(err.value)
    assert "test-key" not in str(err.value)


def test_comparison_series_uses_adjusted_prices_across_a_split():
    def split_rows():
        before = bar("2025-01-06", code="13060", close=3000)
        before.update(AdjO=300, AdjH=301, AdjL=299, AdjC=300, AdjVo=10000)
        ex_date = bar("2025-01-07", code="13060", close=305, factor=0.1)
        ex_date.update(AdjO=304, AdjH=306, AdjL=303, AdjC=305, AdjVo=12000)
        return {"data": [before, ex_date]}

    data, _ = client(Response(split_rows()))
    data.preload_prices("1306", "2025-01-01", "2025-01-31", adjusted=True)
    prices = data.get_prices("1306.T", "2025-01-01", "2025-01-31")
    assert [p.close for p in prices] == [300, 305]
    assert prices[0].volume == 10000

    traded, _ = client(Response(split_rows()))
    with pytest.raises(JQuantsDataError, match="split/consolidation"):
        traded.preload_prices("1306", "2025-01-01", "2025-01-31")


def test_financial_metrics_fetch_once_and_value_at_disclosure_close():
    fy = dict(DocType="FYFinancialStatements_Consolidated_JP", CurPerType="FY",
              CurFYSt="2024-04-01", CurFYEn="2025-03-31", CurPerEn="2025-03-31",
              DiscDate="2025-05-08", DiscTime="15:00:00", Sales="1000",
              OP="100", NP="50", EPS="50", ShEq="2000", TA="4000",
              ShOutFY="1", TrShFY="0", BPS="2000", CFO="", CFI="")
    # A split before the disclosure must not stop valuation-only prices.
    split_day = bar("2025-05-01", close=900, factor=0.5)
    data, session = client(
        Response({"data": [fy]}),
        Response({"data": [split_day, bar("2025-05-08", close=1000)]}),
    )
    first = data.get_financial_metrics("7203.T", "2025-06-01")
    again = data.get_financial_metrics("7203", "2025-07-01")
    assert first == again and first[0].price_to_earnings_ratio == 20
    assert [c[0].rsplit("/", 2)[-2:] for c in session.calls] == [
        ["fins", "summary"], ["bars", "daily"]]
    assert session.calls[1][1]["params"] == {
        "code": "7203", "from": "2025-04-24", "to": "2025-05-08"}


def test_company_facts_map_tse_sectors():
    data, _ = client(Response({"data": [
        {"Date": "2026-07-02", "Code": "72030", "CoName": "トヨタ自動車",
         "CoNameEn": "TOYOTA MOTOR CORPORATION", "S17Nm": "自動車・輸送機",
         "S33Nm": "輸送用機器", "MktNm": "プライム"}]}))
    facts = data.get_company_facts("7203")
    assert (facts.ticker, facts.sector, facts.industry) == ("7203", "自動車・輸送機", "輸送用機器")
    assert data.get_company_facts("7203") is facts



class RateLimited(Response):
    status_code = 429

    def __init__(self, retry_after=None):
        super().__init__({"message": "Rate limit exceeded"}, status=429)
        self.headers = {} if retry_after is None else {"Retry-After": retry_after}

    def raise_for_status(self):
        raise requests.HTTPError("HTTP 429", response=self)


def test_rate_limit_waits_and_retries(monkeypatch):
    waits = []
    monkeypatch.setattr("hedge_fund.data.jquants.time.sleep", waits.append)
    data, session = client(RateLimited("7"), RateLimited(),
                           Response({"data": [bar("2025-01-06")]}))
    prices = data.get_prices("7203", "2025-01-01", "2025-01-31")
    assert [p.close for p in prices] == [2000] and len(session.calls) == 3
    assert 7.0 in waits and 60.0 in waits


def test_rate_limit_gives_up_after_retries(monkeypatch):
    monkeypatch.setattr("hedge_fund.data.jquants.time.sleep", lambda _: None)
    data, _ = client(*[RateLimited() for _ in range(4)])
    with pytest.raises(JQuantsDataError, match="HTTP 429 Rate limit exceeded"):
        data.get_prices("7203", "2025-01-01", "2025-01-31")


def test_requests_are_spaced_with_headroom():
    assert JQuantsPriceClient(api_key="k")._interval == pytest.approx(13.2)


def test_adjust_splits_records_forward_splits_and_stops_on_consolidation():
    rows = {"data": [bar("2025-12-26", code="80010", close=10000),
                     bar("2025-12-29", code="80010", close=2000, factor=0.2)]}
    session = Session(Response(rows))
    data = JQuantsPriceClient(api_key="k", session=session, requests_per_minute=1_000_000,
                              adjust_splits=True)
    data.preload_prices("8001", "2025-12-01", "2026-01-31")
    assert [p.close for p in data.get_prices("8001", "2025-12-01", "2026-01-31")] == [10000, 2000]
    assert data.share_splits("8001.T", "2025-12-26", "2026-01-09") == [("2025-12-29", 5.0)]
    assert data.share_splits("8001", "2025-12-29", "2026-01-09") == []   # ex-date not after

    merge = Session(Response({"data": [bar("2025-12-29", code="80010", factor=10.0)]}))
    data = JQuantsPriceClient(api_key="k", session=merge, requests_per_minute=1_000_000,
                              adjust_splits=True)
    with pytest.raises(JQuantsDataError, match="consolidations are not modelled"):
        data.get_prices("8001", "2025-12-01", "2026-01-31")


def test_valuation_prices_fall_back_to_plan_coverage():
    fy = dict(DocType="FYFinancialStatements_Consolidated_JP", CurPerType="FY",
              CurFYSt="2023-09-01", CurFYEn="2024-08-31", CurPerEn="2024-08-31",
              DiscDate="2024-10-10", DiscTime="15:00:00", Sales="1000", OP="100",
              NP="50", EPS="50", ShEq="2000", TA="4000", ShOutFY="1", TrShFY="0",
              BPS="2000", CFO="", CFI="")
    old = dict(fy, CurPerType="3Q", CurPerEn="2024-05-31", DiscDate="2024-07-05",
               DocType="3QFinancialStatements_Consolidated_JP")
    refused = ErrorResponse({"message": "Your subscription covers the following dates: "
                                        "2024-07-02 ~ 2026-07-02. If you want more data..."})
    data, session = client(Response({"data": [old, fy]}), refused,
                           Response({"data": [bar("2024-10-10", close=1000)]}))
    metrics = data.get_financial_metrics("7203", "2024-12-01")
    assert metrics[0].price_to_earnings_ratio == 20
    assert session.calls[2][1]["params"]["from"] == "2024-07-02"


def test_market_timezone_moves_the_completed_session_cutoff(monkeypatch):
    from datetime import datetime, timezone

    from hedge_fund.data import sessions

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):  # 2026-09-25 23:30 UTC = 09-26 08:30 Tokyo = 09-25 19:30 New York
            return datetime(2026, 9, 25, 23, 30, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(sessions, "datetime", Clock)
    monkeypatch.delenv("HEDGE_FUND_MARKET_TZ", raising=False)
    assert sessions.completed_through() == "2026-09-24"
    monkeypatch.setenv("HEDGE_FUND_MARKET_TZ", "Asia/Tokyo")
    assert sessions.completed_through() == "2026-09-25"
