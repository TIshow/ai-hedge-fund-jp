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
    weekdays = [(first + timedelta(days=i)).isoformat() for i in range(60)
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


class SplittingBars(DailyBars):
    """Prices halve on a 2-for-1 ex-date; the provider reports the split."""

    def __init__(self, prices, splits):
        super().__init__(prices)
        self.splits = splits

    def share_splits(self, ticker, after, through):
        return [(d, r) for t, d, r in self.splits if t == ticker and after < d <= through]


def test_forward_split_doubles_held_shares_and_keeps_nav_continuous(monkeypatch):
    # An always-long view isolates the split; momentum itself would read the
    # unadjusted halving as a loss, which is why the CLI keeps it split-free.
    from hedge_fund.models import Signal
    monkeypatch.setattr(
        "hedge_fund.signals.momentum.MomentumModel.predict",
        lambda self, ticker, date, data: Signal(model_name="momentum", ticker=ticker,
                                                date=date, value=1.0, reasoning="long"))
    days, series = prices()
    # Bought at the Mon 2/10 close; the 2-for-1 ex-date falls before the next
    # execution (Mon 2/17), so only the split can change the share count.
    ex_date = "2025-02-12"
    series["7203"] = {d: (c / 2 if d >= ex_date else c) for d, c in series["7203"].items()}
    data = SplittingBars(series, [("7203", ex_date, 2.0)])
    result = backtest_fund(Fund(load_spec("japan-pilot.yaml")), "2025-02-03", "2025-02-21",
                           data, ["7203"])
    first, second = result.records[0], result.records[1]
    held = first.positions["7203"]
    assert first.execution_as_of == "2025-02-10" and held > 0
    assert abs(second.positions["7203"] - 2 * held) <= 100   # split, then at most one lot of rebalance
    i = result.dates.index(ex_date)
    # Without the split the book would lose about half the position's value.
    assert result.nav[i] > result.nav[i - 1] * 0.9
