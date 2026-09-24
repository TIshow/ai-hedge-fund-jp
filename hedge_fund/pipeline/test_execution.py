"""build_orders tests — pure diffing math."""

import pytest

from hedge_fund.brokers.models import Position
from hedge_fund.pipeline.execution import build_orders


def _positions(**shares):
    return {t: Position(ticker=t, shares=s) for t, s in shares.items()}


def test_floor_sizing_never_overshoots():
    orders = build_orders({"AAPL": 0.25}, {}, {"AAPL": 300.0}, equity=10_000.0)
    assert len(orders) == 1
    assert orders[0].side == "buy"
    assert orders[0].quantity == 8  # 2500 / 300 = 8.33 -> 8


def test_delta_against_existing_position():
    orders = build_orders(
        {"AAPL": 0.25}, _positions(AAPL=5), {"AAPL": 250.0}, equity=10_000.0,
    )
    assert len(orders) == 1
    assert orders[0].side == "buy"
    assert orders[0].quantity == 5  # target 10, held 5


def test_held_name_missing_from_targets_is_closed():
    orders = build_orders({}, _positions(AAPL=7), {"AAPL": 100.0}, equity=10_000.0)
    assert len(orders) == 1
    assert orders[0].side == "sell"
    assert orders[0].quantity == 7


def test_subshare_delta_emits_nothing():
    orders = build_orders({"AAPL": 0.005}, {}, {"AAPL": 100.0}, equity=10_000.0)
    assert orders == []  # 50 dollars / 100 = 0.5 shares -> floor 0


def test_sells_before_buys_alphabetical():
    orders = build_orders(
        {"AAPL": 0.2, "MSFT": 0.0, "NVDA": 0.2, "AMZN": 0.0},
        _positions(MSFT=10, NVDA=1, AMZN=5),
        {"AAPL": 100.0, "MSFT": 100.0, "NVDA": 100.0, "AMZN": 100.0},
        equity=10_000.0,
    )
    assert [(o.ticker, o.side) for o in orders] == [
        ("AMZN", "sell"), ("MSFT", "sell"),
        ("AAPL", "buy"), ("NVDA", "buy"),
    ]


def test_short_target_sells_past_zero():
    orders = build_orders({"AAPL": -0.2}, {}, {"AAPL": 100.0}, equity=10_000.0)
    assert len(orders) == 1
    assert orders[0].side == "sell"
    assert orders[0].quantity == 20


def test_100_share_lots_keep_residual_cash_and_close_entire_lots():
    orders = build_orders({"7203": 0.4}, {}, {"7203": 3_000},
                          equity=1_000_000, lot_size=100)
    assert [(o.side, o.quantity) for o in orders] == [("buy", 100)]
    assert build_orders({"7203": 0.25}, {}, {"7203": 3_000},
                        equity=1_000_000, lot_size=100) == []
    sell = build_orders({}, _positions(**{"7203": 200}), {"7203": 3_000},
                        equity=1_000_000, lot_size=100)
    assert [(o.side, o.quantity) for o in sell] == [("sell", 200)]


def test_rejects_incompatible_existing_lot():
    with pytest.raises(ValueError, match="violates 100-share lots"):
        build_orders({}, _positions(**{"7203": 25}), {"7203": 3_000},
                     equity=1_000_000, lot_size=100)
