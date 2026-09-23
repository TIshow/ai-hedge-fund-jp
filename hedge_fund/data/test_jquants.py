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
    data, _ = client(Response({}, status=429))
    with pytest.raises(JQuantsDataError, match="request failed"):
        data.get_prices("7203", "2025-01-01", "2025-01-31")


def test_rejects_non_jpx_ticker_and_intraday():
    data, _ = client()
    with pytest.raises(ValueError, match="JPX code"):
        data.get_prices("AAPL", "2025-01-01", "2025-01-31")
    with pytest.raises(ValueError, match="daily"):
        data.get_prices("7203", "2025-01-01", "2025-01-31", interval="hour")
