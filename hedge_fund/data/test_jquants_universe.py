"""Stratified, seeded, point-in-time universe selection."""

import pytest

from hedge_fund.data.jquants_universe import candidates, short_code, stratified_sample


def issue(code, tier, market="プライム", sector="電機・精密"):
    return {"Code": code, "ScaleCat": tier, "MktNm": market, "S17Nm": sector}


MASTER = (
    [issue(f"{1000 + i}0", "TOPIX Core30") for i in range(6)]
    + [issue(f"{2000 + i}0", "TOPIX Mid400") for i in range(6)]
    + [issue("30010", "TOPIX Core30", sector="銀行"),
       issue("30020", "TOPIX Mid400", market="スタンダード"),
       issue("30030", "TOPIX Small 1")]
)


def test_candidates_keep_prime_tiers_and_drop_financials():
    pools = candidates(MASTER, tiers=("TOPIX Core30", "TOPIX Mid400"))
    assert [len(v) for v in pools.values()] == [6, 6]
    with_fin = candidates(MASTER, tiers=("TOPIX Core30",), exclude_financials=False)
    assert "30010" in {r["Code"] for r in with_fin["TOPIX Core30"]}


def test_sample_is_seeded_and_skips_ineligible_with_reasons():
    pools = candidates(MASTER, tiers=("TOPIX Core30", "TOPIX Mid400"))
    rule = lambda r: "too new" if r["Code"] == "10020" else None
    first = stratified_sample(pools, 2, seed=7, eligible=rule)
    again = stratified_sample(pools, 2, seed=7, eligible=rule)
    other = stratified_sample(pools, 2, seed=8, eligible=rule)
    assert first == again and first != other
    selected, skipped = first
    assert [r["tier"] for r in selected] == ["TOPIX Core30"] * 2 + ["TOPIX Mid400"] * 2
    assert all(r["Code"] != "10020" for r in selected)
    assert all(r["reason"] == "too new" for r in skipped)


def test_sample_fails_loudly_when_a_tier_runs_out():
    pools = candidates(MASTER, tiers=("TOPIX Core30",))
    with pytest.raises(ValueError, match="only 0 eligible"):
        stratified_sample(pools, 1, seed=1, eligible=lambda r: "no")


def test_short_code():
    assert short_code("72030") == "7203" and short_code("13015") == "13015"
