"""Select a research universe by rule from J-Quants data as of a start date.

Example:
    .venv/bin/python japan_universe.py --as-of 2026-02-09 --end 2026-06-26 \
        --per-tier 10 --seed 20260209 --out outputs/japan-universe-2026-02-09.json

Selection uses only data public before --as-of: the issue master on that date
and 決算短信 summaries disclosed before it. After selecting, it reports (but
never filters on) splits inside the backtest window and each name's highest
close, because filtering on those would use information from after --as-of.
Prints the comma-separated tickers on stdout for `aihf --tickers`.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

from hedge_fund.data import JQuantsPriceClient
from hedge_fund.data.jquants_fins import to_metrics
from hedge_fund.data.jquants_universe import TIERS, candidates, short_code, stratified_sample
from hedge_fund.features.snapshot import MIN_PERIODS


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--as-of", required=True, help="selection date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="backtest end, for diagnostics only")
    parser.add_argument("--per-tier", type=int, default=10)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--include-financials", action="store_true")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    load_dotenv(".env", override=False)

    with JQuantsPriceClient() as client:
        master = client.listed_issues(args.as_of)
        pools = candidates(master, TIERS, exclude_financials=not args.include_financials)
        print(f"candidates: { {t: len(v) for t, v in pools.items()} }", file=sys.stderr)

        def eligible(row: dict) -> str | None:
            code = short_code(row["Code"])
            rows = client.financial_summaries(code)
            n = len(to_metrics(code, rows, [], args.as_of, limit=100))
            if n < MIN_PERIODS:
                return f"{n} TTM periods disclosed before {args.as_of} (need {MIN_PERIODS})"
            return None

        selected, skipped = stratified_sample(pools, args.per_tier, args.seed, eligible)

        window_start = (date.fromisoformat(args.as_of) - timedelta(days=90)).isoformat()
        for row in selected:
            code = short_code(row["Code"])
            bars = client._get_rows(client.BASE_URL,
                                    {"code": code, "from": window_start, "to": args.end},
                                    f"daily bars for {code}")
            row["splits_in_window"] = sorted(
                b["Date"] for b in bars
                if b.get("AdjFactor") not in (None, "") and float(b["AdjFactor"]) != 1.0)
            closes = [float(b["C"]) for b in bars if b.get("C") not in (None, "")]
            row["max_close_in_window"] = max(closes) if closes else None
            print(f"  {row['tier']:<14} {code}  {row['CoName']}  ({row['S17Nm']})"
                  + (f"  split {row['splits_in_window']}" if row["splits_in_window"] else ""),
                  file=sys.stderr)

    tickers = [short_code(r["Code"]) for r in selected]
    record = {
        "rule": {
            "as_of": args.as_of, "seed": args.seed, "per_tier": args.per_tier,
            "tiers": list(TIERS), "market": "プライム",
            "exclude_financials": not args.include_financials,
            "eligibility": f">= {MIN_PERIODS} TTM periods disclosed before as_of",
            "diagnostics_window": [window_start, args.end],
        },
        "candidates": {t: len(v) for t, v in pools.items()},
        "tickers": tickers,
        "selected": [
            {"ticker": short_code(r["Code"]), "tier": r["tier"], "name": r["CoName"],
             "name_en": r.get("CoNameEn"), "sector17": r["S17Nm"], "sector33": r["S33Nm"],
             "splits_in_window": r["splits_in_window"],
             "max_close_in_window": r["max_close_in_window"]}
            for r in selected
        ],
        "skipped": [{"ticker": short_code(r["Code"]), "tier": r["tier"], "reason": r["reason"]}
                    for r in skipped],
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(",".join(tickers))


if __name__ == "__main__":
    main()
