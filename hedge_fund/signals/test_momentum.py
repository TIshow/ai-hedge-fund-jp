"""A current-day close must never enter a signal traded at that close."""

from datetime import date, timedelta

from hedge_fund.data.models import Price
from hedge_fund.signals.momentum import MomentumModel


class FakePrices:
    def __init__(self):
        self.calls = []

    def get_prices(self, ticker, start_date, end_date):
        self.calls.append((ticker, start_date, end_date))
        first = date(2025, 1, 1)
        days = [first + timedelta(days=i) for i in range(30)]
        return [Price(time=f"{day}T00:00:00+09:00", open=price,
                      high=price, low=price, close=price, volume=1000)
                for day, price in zip(days, [100] * 29 + [10000])
                if start_date <= day.isoformat() <= end_date]


def test_only_prior_closes_used():
    data = FakePrices()
    signal = MomentumModel().predict("7203", "2025-01-30", data)
    assert data.calls[0][2] == "2025-01-29"
    assert signal.value == 0.0
