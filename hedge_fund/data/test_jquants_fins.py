"""決算短信 summary -> FinancialMetrics: point in time, TTM, and valuation."""

import pytest

from hedge_fund.data.jquants_fins import known_statements, to_metrics
from hedge_fund.data.models import Price


def summary(per, fy_start, per_end, disc, *, sales, op, np_, eps,
            doc="FinancialStatements_Consolidated_IFRS", time="13:30:00", **extra):
    fy_end = f"{int(fy_start[:4]) + 1}-03-31"
    row = dict(DocType=f"{per}{doc}", CurPerType=per, CurFYSt=fy_start,
               CurFYEn=fy_end, CurPerEn=per_end, DiscDate=disc, DiscTime=time,
               Sales=str(sales), OP=str(op), NP=str(np_), EPS=str(eps),
               CFO="", CFI="", ShEq="500000", TA="1000000", EqAR="0.5",
               ShOutFY="1100", TrShFY="100", BPS="")
    row.update({k: str(v) for k, v in extra.items()})
    return row


ROWS = [
    # FY2024 (Apr 2024 - Mar 2025): 1Q cumulative 100, FY 400.
    summary("1Q", "2024-04-01", "2024-06-30", "2024-08-01",
            sales=100, op=10, np_=5, eps=5),
    summary("FY", "2024-04-01", "2025-03-31", "2025-05-08",
            sales=400, op=40, np_=20, eps=20, PayoutRatioAnn="0.3"),
    # FY2025 1Q cumulative 120 -> TTM sales 400 + 120 - 100 = 420.
    summary("1Q", "2025-04-01", "2025-06-30", "2025-08-07",
            sales=120, op=15, np_=8, eps=8),
    summary("", "2025-04-01", "2025-06-30", "2025-07-01",
            doc="EarnForecastRevision", sales=999, op=999, np_=999, eps=999),
]
PRICES = [Price(time=f"{d}T00:00:00+09:00", open=p, high=p, low=p, close=p, volume=1)
          for d, p in [("2025-05-07", 900), ("2025-05-08", 1000),
                       ("2025-08-06", 1100), ("2025-08-07", 1200)]]


def test_disclosure_is_usable_only_after_its_date():
    assert [m.report_period for m in to_metrics("7203", ROWS, PRICES, "2025-08-07")] \
        == ["2025-03-31"]
    assert [m.report_period for m in to_metrics("7203", ROWS, PRICES, "2025-08-08")] \
        == ["2025-06-30", "2025-03-31"]


def test_quarter_ttm_valuation_and_growth():
    q1 = to_metrics("7203", ROWS, PRICES, "2025-08-08")[0]
    shares = 1000  # issued 1,100 minus 100 treasury
    assert q1.operating_margin == pytest.approx(45 / 420)
    assert q1.net_margin == pytest.approx(23 / 420)
    assert q1.earnings_per_share == pytest.approx(23)
    assert q1.market_cap == pytest.approx(1200 * shares)       # close on DiscDate
    assert q1.price_to_earnings_ratio == pytest.approx(1200 / 23)
    assert q1.book_value_per_share == pytest.approx(500)       # ShEq / shares
    assert q1.return_on_equity == pytest.approx(23 / 500000)
    assert q1.revenue_growth == pytest.approx(0.2)             # 120 vs 100 same YTD
    assert q1.gross_margin is None and q1.debt_to_equity is None
    assert q1.filing_datetime == "2025-08-07T13:30:00+09:00"
    assert q1.currency == "JPY" and q1.period == "ttm"


def test_quarter_without_prior_year_is_omitted_not_annualised():
    assert to_metrics("7203", ROWS[:1], PRICES, "2025-01-01") == []


def test_correction_replaces_original_only_once_disclosed():
    corrected = summary("FY", "2024-04-01", "2025-03-31", "2025-06-01",
                        sales=410, op=40, np_=20, eps=20)
    rows = ROWS + [corrected]
    before = known_statements(rows, "2025-06-01")
    after = known_statements(rows, "2025-06-02")
    assert [r["Sales"] for r in before if r["CurPerType"] == "FY"] == ["400"]
    assert [r["Sales"] for r in after if r["CurPerType"] == "FY"] == ["410"]


def test_forecast_revisions_are_not_results():
    kinds = {r["CurPerType"] for r in known_statements(ROWS, "2025-12-31")}
    assert "" not in kinds


def test_annual_period_and_limit():
    annual = to_metrics("7203", ROWS, PRICES, "2025-12-31", period="annual")
    assert [m.report_period for m in annual] == ["2025-03-31"]
    assert annual[0].payout_ratio == pytest.approx(0.3)
    assert len(to_metrics("7203", ROWS, PRICES, "2025-12-31", limit=1)) == 1
    with pytest.raises(ValueError, match="ttm or annual"):
        to_metrics("7203", ROWS, PRICES, "2025-12-31", period="quarterly")


def test_ttm_eps_stays_on_current_share_basis_across_a_split():
    # 1-for-5 split between the prior-year 1Q (EPS on 100 shares) and now.
    rows = [
        summary("1Q", "2024-04-01", "2024-06-30", "2024-08-01",
                sales=100, op=10, np_=50, eps=0.5, AvgSh=100),
        summary("FY", "2024-04-01", "2025-03-31", "2025-05-08",
                sales=400, op=40, np_=200, eps=0.4, AvgSh=500),
        summary("1Q", "2025-04-01", "2025-06-30", "2025-08-07",
                sales=100, op=10, np_=50, eps=0.1, AvgSh=500),
    ]
    q1 = to_metrics("6758", rows, PRICES, "2025-08-08")[0]
    assert q1.earnings_per_share == pytest.approx(200 / 500)   # not 0.1 + 0.4 - 0.5
    assert q1.earnings_per_share_growth is None
    assert q1.earnings_growth == pytest.approx(0.0)            # net income 50 vs 50
