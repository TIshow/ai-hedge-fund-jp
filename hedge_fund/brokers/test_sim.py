"""SimBroker tests — deterministic fills and bookkeeping."""

import pytest

from hedge_fund.brokers.models import Order
from hedge_fund.brokers.sim import SimBroker


def test_buy_updates_cash_and_position():
    broker = SimBroker(cash=10_000.0)
    fill = broker.place_order(Order(ticker="AAPL", side="buy", quantity=10, price=100.0))
    assert broker.cash() == pytest.approx(9_000.0)
    assert broker.positions()["AAPL"].shares == 10
    assert fill.quantity == 10
    assert fill.price == 100.0


def test_sell_updates_cash_and_position():
    broker = SimBroker(cash=0.0)
    broker.place_order(Order(ticker="AAPL", side="buy", quantity=10, price=100.0))
    broker.place_order(Order(ticker="AAPL", side="sell", quantity=4, price=110.0))
    assert broker.positions()["AAPL"].shares == 6
    assert broker.cash() == pytest.approx(-1_000.0 + 440.0)


def test_position_removed_at_zero():
    broker = SimBroker(cash=1_000.0)
    broker.place_order(Order(ticker="AAPL", side="buy", quantity=5, price=100.0))
    broker.place_order(Order(ticker="AAPL", side="sell", quantity=5, price=100.0))
    assert broker.positions() == {}


def test_sell_past_zero_creates_short():
    broker = SimBroker(cash=0.0)
    broker.place_order(Order(ticker="AAPL", side="sell", quantity=3, price=100.0))
    assert broker.positions()["AAPL"].shares == -3
    assert broker.cash() == pytest.approx(300.0)


def test_nonpositive_price_raises():
    broker = SimBroker(cash=1_000.0)
    with pytest.raises(ValueError):
        broker.place_order(Order(ticker="AAPL", side="buy", quantity=1, price=0.0))


def test_positions_returns_a_copy():
    broker = SimBroker(cash=1_000.0)
    broker.place_order(Order(ticker="AAPL", side="buy", quantity=5, price=100.0))
    broker.positions().clear()
    assert broker.positions()["AAPL"].shares == 5


def test_japan_lot_broker_rejects_odd_lots():
    broker = SimBroker(cash=1_000_000, lot_size=100)
    with pytest.raises(ValueError, match="multiple of 100 shares"):
        broker.place_order(Order(ticker="7203", side="buy", quantity=99, price=1_000))
    broker.place_order(Order(ticker="7203", side="buy", quantity=100, price=1_000))
    assert broker.positions()["7203"].shares == 100


def test_forward_split_multiplies_whole_lots_and_keeps_cash():
    broker = SimBroker(cash=1_000_000, lot_size=100)
    broker.place_order(Order(ticker="8001", side="buy", quantity=100, price=5_000))
    broker.place_order(Order(ticker="7735", side="sell", quantity=100, price=9_000))
    cash = broker.cash()
    assert broker.apply_split("8001", 5.0) == 500
    assert broker.apply_split("7735", 2.0) == -200
    assert broker.apply_split("7203", 2.0) == 0          # not held: no-op
    assert broker.cash() == cash
    with pytest.raises(ValueError, match="not whole 100-share lots"):
        broker.apply_split("8001", 1.5 / 5)              # 500 -> 150
    assert broker.apply_split("7735", 1.5) == -300      # 3-for-2 of 200 is whole lots
    with pytest.raises(ValueError, match="not whole 100-share lots"):
        broker.apply_split("7735", 0.1)                  # 1-for-10 consolidation: -30
