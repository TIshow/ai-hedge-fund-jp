"""Full pipeline with a synthetic Japanese trading window and whole lots."""

from datetime import date, timedelta

import pytest

from hedge_fund.backtesting.fund import backtest_fund
from hedge_fund.data.models import Price
from hedge_fund.fund.spec import Fund, load_spec


class DailyBars:
    require_current_bar = True

    def __init__(self, prices):
        self.prices = prices

    def get_prices(self, ticker, start_date, end_date, **kwargs):
        return [Price(time=f"{day}T00:00:00+09:00", open=close,
                      high=close, low=close, close=close, volume=1000)
                for day, close in self.prices.get(ticker, {}).items()
                if start_date <= day <= end_date]


def prices():
    first = date(2025, 1, 1)
    weekdays = [(first + timedelta(days=i)).isoformat() for i in range(45)
                if (first + timedelta(days=i)).weekday() < 5]
    return weekdays, {
        "7203": {day: 2000 + 10 * i for i, day in enumerate(weekdays)},
        "1306": {day: 3000 + i for i, day in enumerate(weekdays)},
    }


def test_price_only_backtest_uses_100_share_orders():
    days, series = prices()
    spec = load_spec("japan-pilot.yaml")
    result = backtest_fund(Fund(spec), "2025-02-03", "2025-02-14",
                           DailyBars(series), ["7203"])
    assert result.benchmark == "1306"
    assert result.metrics.n_orders >= 1
    assert all(order.quantity % 100 == 0 for r in result.records for order in r.orders)
    assert all(shares % 100 == 0 for r in result.records
               for shares in r.positions.values())
    assert result.records[0].strategies[0].signals[0].value == 1.0
    assert result.records[0].cash >= 0


def test_halted_held_stock_cannot_be_filled_at_prior_close():
    days, series = prices()
    # The fund buys on the first weekly date and this ticker then stops trading.
    first_week, second_week = "2025-02-07", "2025-02-14"
    series["7203"].pop(second_week)
    with pytest.raises(ValueError, match="cannot value the book"):
        backtest_fund(Fund(load_spec("japan-pilot.yaml")), first_week,
                      second_week, DailyBars(series), ["7203"])
