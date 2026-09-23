"""Execution — turn target weights into delta orders.

Targets are the complete statement of the desired book: any held name absent
from the targets has an implicit target of zero, so close orders fall out of
the same arithmetic as everything else. Pure function; the broker does the
filling.

Later: Almgren-Chriss optimal execution, market impact, fill probability.
"""

from __future__ import annotations

from hedge_fund.brokers.models import Order, Position


def build_orders(
    target_weights: dict[str, float],
    positions: dict[str, Position],
    marks: dict[str, float],
    equity: float,
    lot_size: int = 1,
) -> list[Order]:
    """Diff the target book against the broker's current book.

    Sizing: floor the signed target toward zero to a whole lot,
    never overshoot the target; unallocated cash stays in the book and is
    re-evaluated next cycle. Orders below one share are not emitted.

    Ordering: all sells first, then buys, alphabetical within each group —
    deterministic, and sells free the cash that buys consume within the
    same cycle.

    A KeyError on marks here means a pipeline bug upstream (run_cycle prices
    every tradeable and held name before calling this) — let it raise.
    """
    if lot_size < 1:
        raise ValueError("lot_size must be positive")
    sells: list[Order] = []
    buys: list[Order] = []

    for ticker in sorted(set(target_weights) | set(positions)):
        mark = marks[ticker]
        target_lots = int(target_weights.get(ticker, 0.0) * equity / mark / lot_size)
        target_shares = target_lots * lot_size
        current_shares = positions[ticker].shares if ticker in positions else 0
        if current_shares % lot_size:
            raise ValueError(f"held position {ticker} violates {lot_size}-share lots")
        delta = target_shares - current_shares
        if delta == 0:
            continue
        order = Order(
            ticker=ticker,
            side="buy" if delta > 0 else "sell",
            quantity=abs(delta),
            price=mark,
        )
        (buys if delta > 0 else sells).append(order)

    return sells + buys
