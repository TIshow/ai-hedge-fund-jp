"""A deliberately simple price-only signal for the Japan data pilot."""

from __future__ import annotations

from datetime import date, timedelta

from hedge_fund.data.protocol import DataClient
from hedge_fund.models import Signal
from hedge_fund.signals.base import QuantModel


class MomentumModel(QuantModel):
    """Long only when the previous close exceeds the close 20 sessions earlier.

Today's close is never used to decide a trade simulated at today's close.
This is a plumbing baseline, not a claim of predictive power.
    """

    @property
    def name(self) -> str:
        return "momentum"

    def predict(self, ticker: str, date: str, data_client: DataClient) -> Signal:
        as_of = date_from_iso(date)
        end = as_of - timedelta(days=1)
        start = end - timedelta(days=60)
        prices = sorted(
            data_client.get_prices(ticker, start.isoformat(), end.isoformat()),
            key=lambda p: p.time,
        )
        if len(prices) < 21:
            return Signal(
                model_name=self.name, ticker=ticker, date=date, value=0.0,
                reasoning="Fewer than 21 prior trading closes; no signal",
            )
        ret = prices[-1].close / prices[-21].close - 1.0
        return Signal(
            model_name=self.name, ticker=ticker, date=date,
            value=1.0 if ret > 0 else 0.0,
            reasoning=f"Prior 20-session price return: {ret:+.2%}",
            components={"prior_20_session_return": ret},
        )


def date_from_iso(value: str) -> date:
    return date.fromisoformat(value)
