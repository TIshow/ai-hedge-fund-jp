"""Completed daily data and benchmark-observed trading sessions."""

import os
from datetime import date, datetime, timedelta
from math import isfinite
from zoneinfo import ZoneInfo

from hedge_fund.data.protocol import DataClient

NEW_YORK = ZoneInfo("America/New_York")


def completed_through() -> str:
    """Exclude the current market date, even after the close.

    The market's time zone defaults to New York; HEDGE_FUND_MARKET_TZ (an IANA
    name such as Asia/Tokyo) moves the cutoff for other exchanges.
    """
    zone = os.environ.get("HEDGE_FUND_MARKET_TZ")
    tz = ZoneInfo(zone) if zone else NEW_YORK
    return (datetime.now(tz).date() - timedelta(days=1)).isoformat()


def previous_day(day: str) -> str:
    return (date.fromisoformat(day) - timedelta(days=1)).isoformat()


def session_closes(data: DataClient, benchmark: str, start: str, end: str) -> dict[str, float]:
    end = min(end, completed_through())
    if start > end:
        return {}
    bars = data.get_prices(benchmark, start, end)
    closes = {bar.time[:10]: bar.close for bar in bars if start <= bar.time[:10] <= end}
    for day, close in closes.items():
        if not isfinite(close) or close <= 0:
            raise ValueError(f"{benchmark}: close on {day} must be finite and positive")
    return dict(sorted(closes.items()))
