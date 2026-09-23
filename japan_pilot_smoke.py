"""Run the Japan pilot CLI locally against synthetic J-Quants V2 responses.

This exercises the actual command-line, pagination-free API parsing, pricing,
signal, risk, broker, and backtest result without credentials or network access.
The generated JSON is synthetic and is not an investment performance result.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import requests

from hedge_fund.run import main


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "outputs" / "japan-pilot-smoke.json"


class FakeResponse:
    def __init__(self, rows: list[dict]):
        self.rows = rows

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {"data": self.rows}


def fake_get(self, url, *, params, headers, timeout):
    if url != "https://api.jquants.com/v2/equities/bars/daily":
        raise AssertionError(f"Unexpected network request: {url}")
    assert headers == {"x-api-key": "synthetic-example-key"}
    prices = {"7203": (2000, 12), "6758": (3000, -8), "1306": (2500, 2)}
    code = params["code"]
    base, slope = prices[code]
    first = date(2025, 1, 1)
    days = [first + timedelta(days=i) for i in range(60)]
    traded = [day for day in days if day.weekday() < 5]
    rows = []
    for i, day in enumerate(traded):
        if params["from"] <= day.isoformat() <= params["to"]:
            close = base + slope * i
            rows.append({"Date": day.isoformat(), "Code": code + "0",
                         "O": close - 2, "H": close + 2, "L": close - 4,
                         "C": close, "Vo": 100000, "AdjFactor": 1.0})
    return FakeResponse(rows)


def run() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    argv = ["aihf", str(ROOT / "japan-pilot.yaml"),
            "--data-provider", "jquants", "--tickers", "7203,6758",
            "--backtest", "--start", "2025-02-03", "--date", "2025-02-21",
            "--out", str(OUTPUT)]
    with patch.dict(os.environ, {"JQUANTS_API_KEY": "synthetic-example-key"}), \
         patch.object(requests.Session, "get", fake_get), \
         patch("hedge_fund.data.jquants.time.sleep", return_value=None), \
         patch.object(sys, "argv", argv):
        main()

    result = json.loads(OUTPUT.read_text())
    records = result["records"]
    assert len(records) == 3, "Expected three Friday rebalance cycles"
    assert result["metrics"]["n_orders"] > 0, "Expected at least one order"
    assert all(o["quantity"] % 100 == 0 for r in records for o in r["orders"])
    assert all("6758" not in r["positions"] for r in records)
    print(f"Synthetic smoke run passed: {len(records)} cycles, "
          f"{result['metrics']['n_orders']} order(s), "
          f"end NAV {result['nav'][-1]:,.0f} JPY; output: {OUTPUT}",
          file=sys.stderr)


if __name__ == "__main__":
    run()
