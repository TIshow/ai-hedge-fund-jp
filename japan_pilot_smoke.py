"""Run the Japan pilot CLI locally against synthetic J-Quants V2 responses.

This exercises the actual command-line, pagination-free API parsing, pricing,
signal, risk, broker, and backtest result without credentials or network access,
including a split in the comparison-only benchmark.
The generated JSON is synthetic and is not an investment performance result.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import requests

from hedge_fund.run import main


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "outputs" / "japan-pilot-smoke.json"
BENCHMARK_SPLIT = "2025-02-12"


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
            adj = base + slope * i
            # The benchmark ETF has a 2-for-1 split between rebalance dates;
            # unadjusted closes before it are twice the adjusted series.
            ratio = 2 if code == "1306" and day.isoformat() < BENCHMARK_SPLIT else 1
            close = adj * ratio
            factor = 0.5 if code == "1306" and day.isoformat() == BENCHMARK_SPLIT else 1.0
            rows.append({"Date": day.isoformat(), "Code": code + "0",
                         "O": close - 2, "H": close + 2, "L": close - 4,
                         "C": close, "Vo": 100000 // ratio, "AdjFactor": factor,
                         "AdjO": adj - 2, "AdjH": adj + 2, "AdjL": adj - 4,
                         "AdjC": adj, "AdjVo": 100000})
    return FakeResponse(rows)


def run() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    argv = ["aihf", str(ROOT / "japan-pilot.yaml"),
            "--data-provider", "jquants", "--tickers", "7203,6758",
            "--backtest", "--start", "2025-02-03", "--date", "2025-02-21",
            "--out", str(OUTPUT)]
    with tempfile.TemporaryDirectory() as home, \
         patch.dict(os.environ, {"JQUANTS_API_KEY": "synthetic-example-key"}), \
         patch("hedge_fund.paths.MANDATES_DIR", Path(home) / "mandates"), \
         patch.object(requests.Session, "get", fake_get), \
         patch("hedge_fund.data.jquants.time.sleep", return_value=None), \
         patch.object(sys, "argv", argv):
        main()

    result = json.loads(OUTPUT.read_text())
    records = result["records"]
    # Friday assessments execute at the next session's close (upstream 2.4):
    # 2/7 -> 2/10 and 2/14 -> 2/17; the 2/21 assessment stays pending.
    assert len(records) == 2, "Expected two executed weekly cycles"
    assert len(result["pending"]) == 1, "Expected the last assessment to be pending"
    assert result["metrics"]["n_orders"] > 0, "Expected at least one order"
    assert all(o["quantity"] % 100 == 0 for r in records for o in r["orders"])
    assert all("6758" not in r["positions"] for r in records)
    # Adjusted benchmark closes keep the comparison continuous over the split.
    assert 0 < result["metrics"]["benchmark_return_pct"] < 0.05
    print(f"Synthetic smoke run passed: {len(records)} executed cycles, "
          f"{result['metrics']['n_orders']} order(s), "
          f"end NAV {result['nav'][-1]:,.0f} JPY; output: {OUTPUT}",
          file=sys.stderr)


if __name__ == "__main__":
    run()
