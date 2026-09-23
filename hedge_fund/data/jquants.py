"""J-Quants V2 daily prices for a small, historical Japan-equity pilot.

Only ``get_prices`` is implemented. Financial statements and earnings need
separate point-in-time mappings before the LLM and PEAD models can use this
provider. Prices are unadjusted; a split/consolidation causes an error rather
than silently corrupting the simulated share count.
"""

from __future__ import annotations

import math
import os
import re
import time
from datetime import date

import requests

from hedge_fund.data.models import Price


class JQuantsDataError(RuntimeError):
    """API failure or data that this price-only pilot cannot model safely."""


class JQuantsPriceClient:
    BASE_URL = "https://api.jquants.com/v2/equities/bars/daily"
    require_current_bar = True  # Avoid simulated fills on suspended trading days.

    def __init__(
        self,
        api_key: str | None = None,
        *,
        requests_per_minute: int = 5,
        timeout: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        self._key = api_key or os.environ.get("JQUANTS_API_KEY", "")
        if not self._key:
            raise ValueError("Set JQUANTS_API_KEY before using J-Quants V2")
        if requests_per_minute < 1:
            raise ValueError("requests_per_minute must be positive")
        self._interval = 60.0 / requests_per_minute
        self._last_request: float | None = None
        self._timeout = timeout
        self._session = session or requests.Session()
        self._preloaded: dict[str, tuple[str, str, list[Price]]] = {}

    def __enter__(self) -> JQuantsPriceClient:
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def close(self) -> None:
        self._session.close()

    @staticmethod
    def _code(ticker: str) -> str:
        code = ticker.upper().removesuffix(".T")
        if not re.fullmatch(r"[0-9]{4,5}", code):
            raise ValueError(f"J-Quants requires a 4- or 5-digit JPX code: {ticker!r}")
        return code

    def preload_prices(self, ticker: str, start_date: str, end_date: str) -> None:
        """Fetch a whole window once, avoiding a request for every backtest tick."""
        code = self._code(ticker)
        self._preloaded[code] = (
            start_date, end_date, self._fetch_prices(code, start_date, end_date)
        )

    def get_prices(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
        interval: str = "day",
        interval_multiplier: int = 1,
    ) -> list[Price]:
        if interval != "day" or interval_multiplier != 1:
            raise ValueError("J-Quants pilot supports only unaggregated daily bars")
        code = self._code(ticker)
        cached = self._preloaded.get(code)
        if cached is not None and cached[0] <= start_date <= end_date <= cached[1]:
            return [p for p in cached[2] if start_date <= p.time[:10] <= end_date]
        return self._fetch_prices(code, start_date, end_date)

    def _fetch_prices(self, code: str, start_date: str, end_date: str) -> list[Price]:
        try:
            if date.fromisoformat(start_date) > date.fromisoformat(end_date):
                raise ValueError("start_date must be on or before end_date")
        except ValueError as exc:
            raise ValueError("Expected an ordered YYYY-MM-DD date range") from exc
        params = {"code": code, "from": start_date, "to": end_date}
        prices: list[Price] = []
        seen_pages: set[str] = set()
        while True:
            if self._last_request is not None:
                remaining = self._interval - (time.monotonic() - self._last_request)
                if remaining > 0:
                    time.sleep(remaining)
            try:
                response = self._session.get(
                    self.BASE_URL,
                    params=params.copy(),
                    headers={"x-api-key": self._key},
                    timeout=self._timeout,
                )
                self._last_request = time.monotonic()
                response.raise_for_status()
                payload = response.json()
            except (requests.RequestException, ValueError) as exc:
                raise JQuantsDataError(
                    f"J-Quants daily bars request failed for {code}; "
                    "check the key, plan, rate limit, and date range"
                ) from exc
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise JQuantsDataError("Unexpected J-Quants daily bars response")
            for row in payload["data"]:
                if not isinstance(row, dict):
                    raise JQuantsDataError("Unexpected J-Quants daily bar")
                row_code = str(row.get("Code", ""))
                if row_code not in (code, code + "0"):
                    raise JQuantsDataError(f"Unexpected ticker {row_code!r} in {code} bars")
                day = row.get("Date")
                if not isinstance(day, str):
                    raise JQuantsDataError("J-Quants daily bar has no date")
                try:
                    day = date.fromisoformat(day).isoformat()
                    factor = float(row["AdjFactor"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise JQuantsDataError(f"Missing date or adjustment factor for {code}") from exc
                if not math.isfinite(factor) or not math.isclose(factor, 1.0):
                    raise JQuantsDataError(
                        f"{code} has a split/consolidation on {day}; "
                        "share adjustments are not implemented in this pilot"
                    )
                if not start_date <= day <= end_date:
                    raise JQuantsDataError(f"Unexpected date {day} in {code} bars")
                # No trades on a listed day have null OHLC; they are not prices.
                if all(row.get(k) is None for k in ("O", "H", "L", "C")):
                    continue
                try:
                    values = [float(row[k]) for k in ("O", "H", "L", "C")]
                    volume = int(row["Vo"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise JQuantsDataError(f"Incomplete OHLCV for {code} on {day}") from exc
                if not all(math.isfinite(v) and v > 0 for v in values) or volume < 0:
                    raise JQuantsDataError(f"Invalid OHLCV for {code} on {day}")
                prices.append(Price(
                    time=day + "T00:00:00+09:00", open=values[0],
                    high=values[1], low=values[2], close=values[3], volume=volume,
                ))
            page = payload.get("pagination_key")
            if not page:
                return sorted(prices, key=lambda p: p.time)
            if not isinstance(page, str) or page in seen_pages:
                raise JQuantsDataError("Invalid J-Quants pagination key")
            seen_pages.add(page)
            params["pagination_key"] = page
