"""CLI backtest of an LLM agent on synthetic J-Quants prices and 決算短信.

No network or LLM key: requests.Session.get serves fake J-Quants pages and
the agent's LLM returns a canned bullish view.
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
import requests

from hedge_fund.llm import PromptCache
from hedge_fund.run import main

ROOT = Path(__file__).resolve().parents[2]


class FakeResponse:
    def __init__(self, rows):
        self.rows = rows

    def raise_for_status(self):
        pass

    def json(self):
        return {"data": self.rows}


def _bars(code, start, end):
    base = {"7203": 2000, "1306": 2500}[code]
    day, rows = date.fromisoformat(start), []
    while day.isoformat() <= end:
        if day.weekday() < 5:
            close = base + (day - date(2023, 1, 1)).days
            rows.append({"Date": day.isoformat(), "Code": code + "0",
                         "O": close, "H": close, "L": close, "C": close,
                         "Vo": 1000, "AdjFactor": 1.0, "AdjO": close,
                         "AdjH": close, "AdjL": close, "AdjC": close, "AdjVo": 1000})
        day += timedelta(days=1)
    return rows


def _summaries():
    rows = []
    for fy, sales in ((2023, 1000), (2024, 1100)):
        for per, end, disc, share in (
            ("1Q", f"{fy}-06-30", f"{fy}-08-01", 0.25),
            ("2Q", f"{fy}-09-30", f"{fy}-11-01", 0.5),
            ("3Q", f"{fy}-12-31", f"{fy + 1}-02-05", 0.75),
            ("FY", f"{fy + 1}-03-31", f"{fy + 1}-05-08", 1.0),
        ):
            rows.append({
                "DocType": f"{per}FinancialStatements_Consolidated_JP",
                "CurPerType": per, "CurFYSt": f"{fy}-04-01",
                "CurFYEn": f"{fy + 1}-03-31", "CurPerEn": end,
                "DiscDate": disc, "DiscTime": "15:00:00",
                "Sales": str(sales * share * 1e6), "OP": str(sales * share * 1e5),
                "NP": str(sales * share * 5e4), "EPS": str(sales * share / 20),
                "ShEq": "5e8", "TA": "1e9", "ShOutFY": "1e6", "TrShFY": "0",
                "BPS": "", "CFO": "", "CFI": "",
            })
    return rows


def fake_get(self, url, *, params, headers, timeout):
    assert headers == {"x-api-key": "synthetic-example-key"}
    if url.endswith("/equities/bars/daily"):
        return FakeResponse(_bars(params["code"], params["from"], params["to"]))
    if url.endswith("/fins/summary"):
        return FakeResponse(_summaries() if params["code"] == "7203" else [])
    if url.endswith("/equities/master"):
        return FakeResponse([{"Date": "2025-02-01", "Code": "72030",
                              "CoNameEn": "SYNTHETIC", "S17Nm": "自動車・輸送機",
                              "S33Nm": "輸送用機器", "MktNm": "プライム"}])
    raise AssertionError(f"Unexpected request: {url}")


class BullishLLM:
    model = "fake-model"

    def __init__(self):
        self.prompts = []

    def complete(self, system, user):
        self.prompts.append(user)
        return json.dumps({"signal": "bullish", "confidence": 80, "reasoning": "r"})


def test_buffett_backtest_on_japanese_summaries(monkeypatch, tmp_path):
    llm = BullishLLM()
    monkeypatch.setenv("JQUANTS_API_KEY", "synthetic-example-key")
    monkeypatch.setattr("hedge_fund.paths.MANDATES_DIR", tmp_path / "mandates")
    monkeypatch.setattr(requests.Session, "get", fake_get)
    monkeypatch.setattr("hedge_fund.data.jquants.time.sleep", lambda _: None)
    monkeypatch.setattr("hedge_fund.signals.llm_agent.make_llm", lambda: llm)
    monkeypatch.setattr("hedge_fund.signals.llm_agent.PromptCache",
                        lambda: PromptCache(tmp_path / "llm"))
    out = tmp_path / "result.json"
    monkeypatch.setattr(sys, "argv", [
        "aihf", str(ROOT / "japan-fundamentals.yaml"), "--data-provider", "jquants",
        "--tickers", "7203", "--backtest", "--start", "2025-01-27",
        "--date", "2025-02-17", "--out", str(out)])

    main()

    result = json.loads(out.read_text())
    signals = [s for r in result["records"] for st in r["strategies"] for s in st["signals"]]
    # Weekly assessments execute at the next session's close (upstream 2.4):
    # Fri 1/31 -> Mon 2/3, Fri 2/7 -> Mon 2/10, Fri 2/14 -> Mon 2/17; the 2/17
    # assessment has no later session in the window and stays pending.
    # 3Q is disclosed 2025-02-05: only 3 TTM periods before then, 4 after.
    assert [s["value"] for s in signals] == [0.0, 0.8, 0.8]
    assert [r["execution_as_of"] for r in result["records"]] == ["2025-02-03", "2025-02-10", "2025-02-17"]
    assert len(result["pending"]) == 1
    assert "insufficient data" in signals[0]["reasoning"]
    orders = [o for r in result["records"] for o in r["orders"]]
    assert orders and all(o["quantity"] % 100 == 0 for o in orders)
    # One LLM call: the unchanged snapshot on the next date is a cache hit.
    assert len(llm.prompts) == 1
    assert "自動車・輸送機" in llm.prompts[0] and "2025-02-05" in llm.prompts[0]


def test_cli_still_rejects_pead_for_jquants(monkeypatch, tmp_path, capsys):
    mandate = tmp_path / "pead.yaml"
    mandate.write_text((ROOT / "japan-pilot.yaml").read_text()
                       .replace("name: momentum", "name: pead"))
    monkeypatch.setenv("JQUANTS_API_KEY", "synthetic-example-key")
    monkeypatch.setattr("hedge_fund.paths.MANDATES_DIR", tmp_path / "mandates")
    monkeypatch.setattr(sys, "argv", ["aihf", str(mandate), "--data-provider",
                                      "jquants", "--tickers", "7203"])
    with pytest.raises(SystemExit):
        main()
    assert "unsupported: pead" in capsys.readouterr().err
