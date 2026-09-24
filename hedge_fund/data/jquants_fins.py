"""Map J-Quants V2 financial summaries (決算短信) onto FinancialMetrics.

Pure functions, no I/O: the client fetches rows and closes, this module
decides what was knowable and how Japanese cumulative figures become the
trailing-twelve-month rows the LLM snapshot expects.

Point in time: a disclosure is usable on *end_date* only if its DiscDate is
strictly before it. Simulated trades fill at the close, and many summaries
are released at or after 15:00, so same-day disclosures are excluded.

Japanese quarterly summaries are cumulative from the fiscal-year start.
TTM for a quarter is ``current YTD + prior FY - prior-year same YTD``; a row
without that history is omitted rather than annualised. Growth rates compare
the same cumulative period year over year (前年同期比), which equals TTM
growth for full-year rows. TTM EPS is TTM net income over the latest
average share count, so a stock split inside the window does not mix bases.
The summary has no gross profit, debt, or current assets, so gross margin,
debt/equity, and current ratio stay null.

Known limit: summaries are as originally reported. When a company restates
prior periods (e.g. a spin-off moved to discontinued operations), TTM and
year-over-year figures mix the old and new basis; the free summary has no
restated comparatives to correct this.
"""

from __future__ import annotations

from datetime import date, timedelta

from hedge_fund.data.models import FinancialMetrics, Price

_FLOWS = ("Sales", "OP", "NP", "EPS", "CFO", "CFI")
_PERIOD_TYPES = ("1Q", "2Q", "3Q", "FY")


def _num(row: dict, key: str) -> float | None:
    value = row.get(key)
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def known_statements(rows: list[dict], end_date: str) -> list[dict]:
    """Latest known version of each period's results, disclosed before *end_date*.

    Forecast revisions and other non-results documents are dropped; a
    corrected summary replaces the original once it is itself disclosed.
    """
    latest: dict[tuple[str, str], dict] = {}
    for row in rows:
        if "FinancialStatements" not in str(row.get("DocType", "")):
            continue
        if row.get("CurPerType") not in _PERIOD_TYPES:
            continue
        disc = str(row.get("DiscDate", ""))
        if not disc or disc >= end_date:
            continue
        key = (str(row.get("CurFYSt", "")), str(row["CurPerType"]))
        stamp = (disc, str(row.get("DiscTime", "")))
        prior = latest.get(key)
        if prior is None or stamp > (prior["DiscDate"], str(prior.get("DiscTime", ""))):
            latest[key] = row
    return list(latest.values())


def _previous_fy_start(row: dict) -> str | None:
    try:
        start = date.fromisoformat(row["CurFYSt"])
    except (KeyError, TypeError, ValueError):
        return None
    # A one-year-earlier fiscal year starts on the same day a year before.
    try:
        return start.replace(year=start.year - 1).isoformat()
    except ValueError:  # 29 February
        return (start - timedelta(days=365)).isoformat()


def _ttm(row: dict, by_period: dict[tuple[str, str], dict]) -> dict[str, float | None] | None:
    if row["CurPerType"] == "FY":
        return {k: _num(row, k) for k in _FLOWS}
    prev_start = _previous_fy_start(row)
    prev_fy = by_period.get((prev_start, "FY"))
    prev_same = by_period.get((prev_start, row["CurPerType"]))
    if prev_fy is None or prev_same is None:
        return None
    # The prior fiscal year must end the day before this one starts.
    try:
        contiguous = (
            date.fromisoformat(prev_fy["CurFYEn"]) + timedelta(days=1)
            == date.fromisoformat(row["CurFYSt"])
        )
    except (KeyError, TypeError, ValueError):
        contiguous = False
    if not contiguous:
        return None
    out: dict[str, float | None] = {}
    for key in _FLOWS:
        parts = (_num(row, key), _num(prev_fy, key), _num(prev_same, key))
        out[key] = None if None in parts else parts[0] + parts[1] - parts[2]
    return out


def _ttm_eps(row: dict, flows: dict[str, float | None]) -> float | None:
    """TTM net income over this summary's average shares.

    Summed or differenced EPS mixes share bases across a split (a pre-split
    prior-year quarter minus a post-split one); one divisor keeps it on the
    current basis. Without average shares, fall back to the summed EPS.
    """
    shares = _num(row, "AvgSh")
    if flows["NP"] is not None and shares is not None and shares > 0:
        return flows["NP"] / shares
    return flows["EPS"]


def _yoy(row: dict, by_period: dict[tuple[str, str], dict], key: str) -> float | None:
    prior = by_period.get((_previous_fy_start(row), row["CurPerType"]))
    if prior is None:
        return None
    now, before = _num(row, key), _num(prior, key)
    if now is None or before is None or before <= 0:
        return None
    return now / before - 1


def _close_on_or_before(prices: list[Price], day: str) -> float | None:
    closes = [p for p in prices if p.time[:10] <= day]
    return max(closes, key=lambda p: p.time).close if closes else None


def _book(row: dict) -> tuple[float | None, float | None, float | None]:
    """(owner equity, shares outstanding ex-treasury, book value per share)."""
    equity = _num(row, "ShEq")
    if equity is None:
        ratio, assets = _num(row, "EqAR"), _num(row, "TA")
        equity = ratio * assets if ratio is not None and assets is not None else None
    issued, treasury = _num(row, "ShOutFY"), _num(row, "TrShFY")
    shares = issued - (treasury or 0.0) if issued is not None else None
    if shares is not None and shares <= 0:
        shares = None
    bps = _num(row, "BPS")
    if bps is None:
        bps = _div(equity, shares)
    return equity, shares, bps


def to_metrics(
    ticker: str,
    rows: list[dict],
    prices: list[Price],
    end_date: str,
    period: str = "ttm",
    limit: int = 10,
) -> list[FinancialMetrics]:
    """Point-in-time metrics, newest first, as the FD client returns them.

    *prices* are unadjusted daily closes; valuation uses the close on or
    before each disclosure date with that summary's own share count.
    """
    if period not in ("ttm", "annual"):
        raise ValueError(f"J-Quants summaries support ttm or annual periods, not {period!r}")
    known = known_statements(rows, end_date)
    by_period = {(r["CurFYSt"], r["CurPerType"]): r for r in known}

    out: list[FinancialMetrics] = []
    for row in known:
        if period == "annual" and row["CurPerType"] != "FY":
            continue
        flows = _ttm(row, by_period)
        if flows is None or (flows["NP"] is None and flows["Sales"] is None):
            continue
        sales, op, np_ = flows["Sales"], flows["OP"], flows["NP"]
        eps = _ttm_eps(row, flows)
        fcf = (flows["CFO"] + flows["CFI"]
               if flows["CFO"] is not None and flows["CFI"] is not None else None)
        equity, shares, bps = _book(row)
        assets = _num(row, "TA")
        close = _close_on_or_before(prices, row["DiscDate"])
        market_cap = close * shares if close is not None and shares is not None else None

        out.append(FinancialMetrics(
            ticker=ticker,
            report_period=row["CurPerEn"],
            period=period,
            currency="JPY",
            filing_date=row["DiscDate"],
            filing_datetime=(f"{row['DiscDate']}T{row['DiscTime']}+09:00"
                             if row.get("DiscTime") else None),
            market_cap=market_cap,
            price_to_earnings_ratio=_div(close, eps) if eps and eps > 0 else None,
            price_to_book_ratio=_div(close, bps) if bps and bps > 0 else None,
            price_to_sales_ratio=_div(market_cap, sales),
            free_cash_flow_yield=_div(fcf, market_cap),
            operating_margin=_div(op, sales),
            net_margin=_div(np_, sales),
            return_on_equity=_div(np_, equity) if equity and equity > 0 else None,
            return_on_assets=_div(np_, assets),
            asset_turnover=_div(sales, assets),
            revenue_growth=_yoy(row, by_period, "Sales"),
            earnings_growth=_yoy(row, by_period, "NP"),
            # EPS growth is left null: a split between the two periods would
            # need the split ratio, which the summary lacks. earnings_growth
            # (net income) carries the same information without that risk.
            operating_income_growth=_yoy(row, by_period, "OP"),
            payout_ratio=_num(row, "PayoutRatioAnn") if row["CurPerType"] == "FY" else None,
            earnings_per_share=eps,
            book_value_per_share=bps,
            free_cash_flow_per_share=_div(fcf, shares),
        ))
    out.sort(key=lambda m: m.report_period, reverse=True)
    return out[:limit]
