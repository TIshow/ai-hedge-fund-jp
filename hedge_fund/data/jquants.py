"""J-Quants V2 data for a small, historical Japan-equity pilot.

Implements daily prices, TTM financial metrics from 決算短信 summaries, and
company facts from the listed issue master — enough for the momentum model
and the LLM investor agents. News, insider trades, and earnings surprises
(PEAD) are not available from J-Quants and are not implemented. Traded prices are unadjusted; a split/consolidation causes an error
rather than silently corrupting the simulated share count. A comparison-only
series (the benchmark) may be preloaded with J-Quants' adjusted prices instead.
"""

from __future__ import annotations

import math
import os
import re
import time
from datetime import date, timedelta

import requests

from hedge_fund.data.jquants_fins import to_metrics
from hedge_fund.data.models import CompanyFacts, FinancialMetrics, Price


class JQuantsDataError(RuntimeError):
    """API failure or data that this price-only pilot cannot model safely."""


class JQuantsCoverageError(JQuantsDataError):
    """The request reaches outside the plan's date coverage (HTTP 400)."""

    def __init__(self, message: str, coverage: tuple[str, str]) -> None:
        super().__init__(message)
        self.coverage = coverage


class JQuantsPriceClient:
    BASE_URL = "https://api.jquants.com/v2/equities/bars/daily"
    FINS_URL = "https://api.jquants.com/v2/fins/summary"
    MASTER_URL = "https://api.jquants.com/v2/equities/master"
    RATE_LIMIT_RETRIES = 3
    require_current_bar = True  # Avoid simulated fills on suspended trading days.

    def __init__(
        self,
        api_key: str | None = None,
        *,
        requests_per_minute: int = 5,
        timeout: float = 30.0,
        session: requests.Session | None = None,
        adjust_splits: bool = False,
    ) -> None:
        # adjust_splits: traded prices stay unadjusted, but a forward split no
        # longer stops the run; it is recorded for share_splits() so the
        # backtest can multiply the held shares on the ex-date.
        self._adjust_splits = adjust_splits
        self._splits: dict[str, dict[str, float]] = {}
        self._key = api_key or os.environ.get("JQUANTS_API_KEY", "")
        if not self._key:
            raise ValueError("Set JQUANTS_API_KEY before using J-Quants V2")
        if requests_per_minute < 1:
            raise ValueError("requests_per_minute must be positive")
        # 10% headroom: exactly 60/n seconds apart can land n+1 requests in
        # one server-side minute (observed as HTTP 429 on the free plan).
        self._interval = 60.0 / requests_per_minute * 1.1
        self._last_request: float | None = None
        self._timeout = timeout
        self._session = session or requests.Session()
        self._preloaded: dict[str, tuple[str, str, list[Price]]] = {}
        self._adjusted: set[str] = set()
        self._summaries: dict[str, list[dict]] = {}
        self._valuation_prices: dict[str, list[Price]] = {}
        self._facts: dict[str, CompanyFacts | None] = {}

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

    def preload_prices(
        self, ticker: str, start_date: str, end_date: str, *, adjusted: bool = False,
    ) -> None:
        """Fetch a whole window once, avoiding a request for every backtest tick.

        ``adjusted=True`` is only for a series that is never traded (the
        benchmark): it returns split-adjusted prices instead of stopping at a
        split, and every later request for that ticker stays adjusted.
        """
        code = self._code(ticker)
        if adjusted:
            self._adjusted.add(code)
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

    def share_splits(self, ticker: str, after: str, through: str) -> list[tuple[str, float]]:
        """Forward splits with ex-date in (after, through], as (date, ratio)
        where ratio = new shares per old share (1 / J-Quants AdjFactor).
        Only splits inside fetched price ranges are known."""
        events = self._splits.get(self._code(ticker), {})
        return [(day, 1.0 / factor) for day, factor in sorted(events.items())
                if after < day <= through]

    def get_financial_metrics(
        self,
        ticker: str,
        end_date: str,
        period: str = "ttm",
        limit: int = 10,
    ) -> list[FinancialMetrics]:
        """TTM metrics from 決算短信 summaries disclosed before *end_date*.

        Each ticker's summaries and the closes on their disclosure dates are
        fetched once and reused for every backtest tick; see jquants_fins for
        the point-in-time and TTM rules.
        """
        code = self._code(ticker)
        rows = self.financial_summaries(code)
        if not rows:
            return []
        if code not in self._valuation_prices:
            days = sorted(str(r.get("DiscDate", "")) for r in rows if r.get("DiscDate"))
            first = (date.fromisoformat(days[0]) - timedelta(days=14)).isoformat()
            # Unadjusted closes match each summary's own per-share figures.
            try:
                prices = self._fetch_prices(code, first, days[-1], mode="raw")
            except JQuantsCoverageError as exc:
                # The oldest summaries predate the plan's price history: value
                # what the plan covers; earlier rows get no market cap or P/E.
                start = exc.coverage[0]
                prices = (self._fetch_prices(code, start, days[-1], mode="raw")
                          if start <= days[-1] else [])
            self._valuation_prices[code] = prices
        return to_metrics(code, rows, self._valuation_prices[code],
                          end_date, period, limit)

    def financial_summaries(self, ticker: str) -> list[dict]:
        """Raw 決算短信 summary rows for one issue, fetched once."""
        code = self._code(ticker)
        if code not in self._summaries:
            self._summaries[code] = self._get_rows(
                self.FINS_URL, {"code": code}, f"financial summaries for {code}"
            )
        return self._summaries[code]

    def listed_issues(self, as_of: str) -> list[dict]:
        """The whole listed issue master as it applied on *as_of*."""
        return self._get_rows(self.MASTER_URL, {"date": as_of},
                              f"listed issue master as of {as_of}")

    def get_company_facts(self, ticker: str) -> CompanyFacts | None:
        """Name and TSE sector from the listed issue master (latest record).

        Like the upstream provider this is latest-only; sector is treated as
        a slow-moving attribute.
        """
        code = self._code(ticker)
        if code not in self._facts:
            rows = self._get_rows(self.MASTER_URL, {"code": code},
                                  f"listed issue master for {code}")
            row = max(rows, key=lambda r: str(r.get("Date", ""))) if rows else None
            self._facts[code] = None if row is None else CompanyFacts(
                ticker=code,
                name=row.get("CoNameEn") or row.get("CoName"),
                sector=row.get("S17Nm") or None,
                industry=row.get("S33Nm") or None,
                category=row.get("MktNm") or None,
                exchange="TSE",
            )
        return self._facts[code]

    def _get_rows(self, url: str, params: dict, what: str) -> list[dict]:
        """All pages of one request, paced to the plan's rate limit."""
        params = dict(params)
        rows: list[dict] = []
        seen_pages: set[str] = set()
        retries = 0
        while True:
            if self._last_request is not None:
                remaining = self._interval - (time.monotonic() - self._last_request)
                if remaining > 0:
                    time.sleep(remaining)
            try:
                response = self._session.get(
                    url,
                    params=params.copy(),
                    headers={"x-api-key": self._key},
                    timeout=self._timeout,
                )
                self._last_request = time.monotonic()
                if (getattr(response, "status_code", None) == 429
                        and retries < self.RATE_LIMIT_RETRIES):
                    # Rate limited: wait as told (or a full minute), then retry.
                    retries += 1
                    time.sleep(_retry_after(response))
                    continue
                response.raise_for_status()
                payload = response.json()
            except (requests.RequestException, ValueError) as exc:
                message = (f"J-Quants request failed for {what}{_error_detail(exc)}; "
                           "check the key, plan coverage, rate limit, and date range")
                coverage = _coverage(exc)
                if coverage is not None:
                    raise JQuantsCoverageError(message, coverage) from exc
                raise JQuantsDataError(message) from exc
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise JQuantsDataError(f"Unexpected J-Quants response for {what}")
            for row in payload["data"]:
                if not isinstance(row, dict):
                    raise JQuantsDataError(f"Unexpected J-Quants row for {what}")
                rows.append(row)
            page = payload.get("pagination_key")
            if not page:
                return rows
            if not isinstance(page, str) or page in seen_pages:
                raise JQuantsDataError("Invalid J-Quants pagination key")
            seen_pages.add(page)
            params["pagination_key"] = page

    def _fetch_prices(
        self, code: str, start_date: str, end_date: str, mode: str | None = None,
    ) -> list[Price]:
        """Daily bars. mode: "strict" (unadjusted, stop at a split),
        "adjusted" (comparison-only series), or "raw" (unadjusted, used only
        for valuation at disclosure dates, never for fills)."""
        if mode is None:
            mode = "adjusted" if code in self._adjusted else "strict"
        try:
            if date.fromisoformat(start_date) > date.fromisoformat(end_date):
                raise ValueError("start_date must be on or before end_date")
        except ValueError as exc:
            raise ValueError("Expected an ordered YYYY-MM-DD date range") from exc
        rows = self._get_rows(
            self.BASE_URL, {"code": code, "from": start_date, "to": end_date},
            f"daily bars for {code} ({start_date} to {end_date})",
        )
        adjusted = mode == "adjusted"
        prices: list[Price] = []
        for row in rows:
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
            split = mode == "strict" and not math.isclose(factor, 1.0)
            if split and self._adjust_splits and 0 < factor < 1:
                self._splits.setdefault(code, {})[day] = factor
                split = False
            if not math.isfinite(factor) or split:
                raise JQuantsDataError(
                    f"{code} has a split/consolidation on {day}; "
                    + ("consolidations are not modelled"
                       if self._adjust_splits else
                       "share adjustments are off (momentum needs split-free "
                       "unadjusted prices)")
                )
            if not start_date <= day <= end_date:
                raise JQuantsDataError(f"Unexpected date {day} in {code} bars")
            # No trades on a listed day have null OHLC; they are not prices.
            keys = ("AdjO", "AdjH", "AdjL", "AdjC") if adjusted else ("O", "H", "L", "C")
            if all(row.get(k) is None for k in keys):
                continue
            try:
                values = [float(row[k]) for k in keys]
                volume = round(float(row["AdjVo" if adjusted else "Vo"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise JQuantsDataError(f"Incomplete OHLCV for {code} on {day}") from exc
            if not all(math.isfinite(v) and v > 0 for v in values) or volume < 0:
                raise JQuantsDataError(f"Invalid OHLCV for {code} on {day}")
            prices.append(Price(
                time=day + "T00:00:00+09:00", open=values[0],
                high=values[1], low=values[2], close=values[3], volume=volume,
            ))
        return sorted(prices, key=lambda p: p.time)

def _error_detail(exc: Exception) -> str:
    """HTTP status and the API's own message, never the request headers."""
    response = getattr(exc, "response", None)
    if response is None:
        return ""
    detail = f": HTTP {response.status_code}"
    try:
        message = response.json().get("message")
    except (ValueError, AttributeError):
        message = None
    if isinstance(message, str) and message:
        detail += f" {message[:200]}"
    return detail


def _retry_after(response) -> float:
    """Seconds to wait after HTTP 429: the Retry-After header, else 60."""
    try:
        seconds = float(response.headers.get("Retry-After", ""))
    except (AttributeError, TypeError, ValueError):
        return 60.0
    return min(max(seconds, 1.0), 300.0)


def _coverage(exc: Exception) -> tuple[str, str] | None:
    """(start, end) from J-Quants' HTTP 400 'Your subscription covers the
    following dates: YYYY-MM-DD ~ YYYY-MM-DD' message, else None."""
    response = getattr(exc, "response", None)
    if response is None or getattr(response, "status_code", None) != 400:
        return None
    try:
        message = str(response.json().get("message", ""))
    except (ValueError, AttributeError):
        return None
    found = re.search(r"covers the following dates:\s*(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})",
                      message)
    return (found.group(1), found.group(2)) if found else None
