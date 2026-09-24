"""Rule-based, point-in-time stock selection from the J-Quants issue master.

A research universe chosen by hand reflects the chooser's hindsight (today's
famous survivors). Here the rule is written down first: from the master as
of the start date, keep Prime-market TOPIX constituents of the requested size
tiers, optionally drop financials, shuffle each tier with a recorded seed,
and walk the shuffled order until each tier has enough eligible names.
Eligibility may only use information public by the start date.
"""

from __future__ import annotations

import random
from typing import Callable

TIERS = ("TOPIX Core30", "TOPIX Large70", "TOPIX Mid400")
FINANCIAL_SECTORS = ("銀行", "金融（除く銀行）")


def short_code(code: str) -> str:
    """'72030' -> '7203'; codes whose fifth digit is not 0 stay 5 digits."""
    return code[:4] if len(code) == 5 and code.endswith("0") else code


def candidates(master: list[dict], tiers=TIERS, exclude_financials: bool = True
               ) -> dict[str, list[dict]]:
    """Prime-market issues per size tier, sorted by code (seed-stable order)."""
    out: dict[str, list[dict]] = {t: [] for t in tiers}
    for row in master:
        if row.get("MktNm") != "プライム" or row.get("ScaleCat") not in out:
            continue
        if exclude_financials and row.get("S17Nm") in FINANCIAL_SECTORS:
            continue
        out[row["ScaleCat"]].append(row)
    for rows in out.values():
        rows.sort(key=lambda r: r["Code"])
    return out


def stratified_sample(
    pools: dict[str, list[dict]],
    per_tier: int,
    seed: int,
    eligible: Callable[[dict], str | None],
) -> tuple[list[dict], list[dict]]:
    """Shuffle each tier with *seed*; take the first *per_tier* eligible rows.

    *eligible* returns None when a row qualifies, else the reason it does
    not. Returns (selected, skipped); each row gains 'tier' and, if skipped,
    'reason'. Raises if a tier runs out of eligible names.
    """
    selected: list[dict] = []
    skipped: list[dict] = []
    for tier, rows in pools.items():
        order = list(rows)
        random.Random(f"{seed}:{tier}").shuffle(order)
        taken = 0
        for row in order:
            if taken == per_tier:
                break
            reason = eligible(row)
            if reason is None:
                selected.append({**row, "tier": tier})
                taken += 1
            else:
                skipped.append({**row, "tier": tier, "reason": reason})
        if taken < per_tier:
            raise ValueError(f"{tier}: only {taken} eligible names (need {per_tier})")
    return selected, skipped
