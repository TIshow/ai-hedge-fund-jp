"""Anonymized rendering: no ticker, sector, size, or calendar in the prompt."""

import json

from hedge_fund.data.models import FinancialMetrics
from hedge_fund.features.snapshot import build_snapshot
from hedge_fund.fund.spec import Fund, FundSpec
from hedge_fund.llm import PromptCache
from hedge_fund.data.models import CompanyFacts


class Data:
    def get_financial_metrics(self, ticker, end_date, period="ttm", limit=10):
        return [FinancialMetrics(
            ticker=ticker, report_period=rp, period="ttm", filing_date=fd,
            market_cap=3.8e13, price_to_earnings_ratio=9.87, return_on_equity=0.1,
            earnings_per_share=295.25, book_value_per_share=3062.82)
            for rp, fd in [("2026-03-31", "2026-05-08"), ("2025-12-31", "2026-02-06"),
                           ("2025-09-30", "2025-11-05"), ("2025-03-31", "2025-05-08")]]

    def get_company_facts(self, ticker):
        return CompanyFacts(ticker=ticker, sector="自動車・輸送機", industry="輸送用機器")


def test_anonymized_render_withholds_identity_and_dates():
    snap = build_snapshot("7203", "2026-06-26", Data())
    text = snap.render_anonymized()
    for secret in ("7203", "自動車", "輸送用機器", "2026", "2025", "38000.0B", "Market cap"):
        assert secret not in text
    assert "latest | 9.87 | 0.10" in text
    assert "\n-3m |" in text and "\n-12m |" in text
    assert "295.25" in text and "3062.82" in text      # per-share figures stay
    assert "7203" in snap.render()                    # default rendering unchanged


class RecordingLLM:
    model = "fake-model"

    def __init__(self):
        self.users = []

    def complete(self, system, user):
        self.users.append(user)
        return json.dumps({"signal": "neutral", "confidence": 50, "reasoning": "r"})


def test_mandate_param_turns_on_anonymized_prompts(monkeypatch, tmp_path):
    llm = RecordingLLM()
    monkeypatch.setattr("hedge_fund.signals.llm_agent.make_llm", lambda: llm)
    monkeypatch.setattr("hedge_fund.signals.llm_agent.PromptCache",
                        lambda: PromptCache(tmp_path))
    spec = FundSpec(schema_version=2, name="anon", strategies=[{"name": "s", "blend": {"mode": "long_only"}, "models": [
        {"name": "buffett", "params": {"anonymize": True}}]}],
        risk={"max_position_pct": 0.5, "max_gross_exposure": 1.0})
    agent = Fund(spec).strategies[0][1][0]
    signal = agent.predict("7203", "2026-06-26", Data())
    assert "7203" not in llm.users[0] and "自動車" not in llm.users[0]
    assert signal.metadata["anonymized"] is True


def test_liabilities_column_appears_only_when_supplied():
    snap = build_snapshot("7203", "2026-06-26", Data())
    before_named, before_anon = snap.render(), snap.render_anonymized()
    assert "liab/E" not in before_named and "liab/E" not in before_anon

    for p in snap.periods:
        p.liabilities_to_equity = 1.5
    snap.liabilities_to_equity_latest = 1.5
    named, anon = snap.render(), snap.render_anonymized()
    for text in (named, anon):
        assert "| D/E | liab/E | curr |" in text
        assert "Total liabilities/equity (latest): 1.50" in text
        assert "not only interest-bearing debt" in text
    # Removing the new column and line gives back the original prompt exactly.
    strip = lambda t: "\n".join(  # noqa: E731
        line.replace(" | 1.50 ", " ").replace("| liab/E ", "")
        for line in t.splitlines() if "Total liabilities/equity" not in line)
    assert strip(named) == before_named and strip(anon) == before_anon
