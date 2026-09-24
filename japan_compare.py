"""Compare named vs anonymized agent verdicts by size tier and familiarity.

Example:
    .venv/bin/python japan_compare.py outputs/japan-30-named.json \
        outputs/japan-30-anon.json outputs/japan-universe-2026-02-09.json \
        [outputs/japan-universe-knowledge.json] > outputs/japan-30-compare.txt

A verdict is one agent's view of one company's snapshot. The snapshot hash is
computed from the fundamentals, not the rendered prompt, so the named and
anonymized runs share it: each pair is the same data read with and without
the company's identity. Descriptive statistics only; with ~30 companies these
are an exploratory look, not a significance test.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

from scipy.stats import spearmanr


def verdicts(path: Path) -> dict[tuple[str, str, str], dict]:
    """(agent, ticker, snapshot hash or abstain date) -> first verdict seen."""
    out: dict[tuple[str, str, str], dict] = {}
    for record in json.loads(path.read_text())["records"]:
        for strategy in record["strategies"]:
            for s in strategy["signals"]:
                m = s["metadata"]
                snap = m.get("snapshot_hash") or f"abstain:{record['as_of']}"
                key = (s["model_name"], s["ticker"], snap)
                if key not in out:
                    out[key] = {
                        "as_of": record["as_of"],
                        "view": "abstain" if m.get("abstained") else m.get("signal"),
                        "confidence": m.get("confidence"),
                        "value": s["value"],
                    }
    return out


def summarize(pairs: list[tuple[dict, dict]]) -> dict:
    def share(side: int, view: str) -> float:
        return mean(p[side]["view"] == view for p in pairs)
    decided = [p for p in pairs if "abstain" not in (p[0]["view"], p[1]["view"])]
    return {
        "verdicts": len(pairs),
        "neutral_named": round(share(0, "neutral"), 3),
        "neutral_anon": round(share(1, "neutral"), 3),
        "abstain_named": round(share(0, "abstain"), 3),
        "abstain_anon": round(share(1, "abstain"), 3),
        "mean_abs_value_named": round(mean(abs(p[0]["value"]) for p in pairs), 3),
        "mean_abs_value_anon": round(mean(abs(p[1]["value"]) for p in pairs), 3),
        "same_view": round(mean(p[0]["view"] == p[1]["view"] for p in decided), 3)
        if decided else None,
        "neutral_named_only": sum(p[0]["view"] == "neutral" != p[1]["view"] for p in decided),
        "neutral_anon_only": sum(p[1]["view"] == "neutral" != p[0]["view"] for p in decided),
    }


def main() -> None:
    if len(sys.argv) not in (4, 5):
        sys.exit(__doc__)
    named, anon = verdicts(Path(sys.argv[1])), verdicts(Path(sys.argv[2]))
    universe = json.loads(Path(sys.argv[3]).read_text())
    knowledge = json.loads(Path(sys.argv[4]).read_text()) if len(sys.argv) == 5 else {}
    tier_of = {c["ticker"]: c["tier"] for c in universe["selected"]}

    missing = sorted(set(named) ^ set(anon))
    pairs = [(named[k], anon[k], k) for k in sorted(set(named) & set(anon))]
    print(f"paired verdicts: {len(pairs)}  unpaired: {len(missing)}")

    groups: dict[tuple[str, str], list] = defaultdict(list)
    per_company: dict[str, list] = defaultdict(list)
    for n, a, (agent, ticker, _) in pairs:
        for g in ((agent, tier_of[ticker]), (agent, "all"), ("all", tier_of[ticker]), ("all", "all")):
            groups[g].append((n, a))
        per_company[ticker].append((n, a))

    report = {"groups": {}, "companies": {}, "correlation": None, "unpaired": [list(k) for k in missing]}
    print("\nagent / tier: verdicts | neutral named→anon | |value| named→anon | same view | neutral only named / only anon")
    for (agent, tier), ps in sorted(groups.items()):
        s = summarize(ps)
        report["groups"][f"{agent} / {tier}"] = s
        print(f"  {agent:<8} {tier:<14} {s['verdicts']:>3} | {s['neutral_named']:.2f}→{s['neutral_anon']:.2f}"
              f" | {s['mean_abs_value_named']:.2f}→{s['mean_abs_value_anon']:.2f}"
              f" | {s['same_view']} | {s['neutral_named_only']} / {s['neutral_anon_only']}")

    gaps, fams = [], []
    for ticker, ps in sorted(per_company.items()):
        s = summarize(ps)
        gap = s["neutral_named"] - s["neutral_anon"]
        fam = knowledge.get(ticker, {}).get("familiarity")
        report["companies"][ticker] = {**s, "tier": tier_of[ticker], "neutral_gap": round(gap, 3),
                                       "familiarity": fam}
        if fam is not None:
            gaps.append(gap)
            fams.append(fam)
    if len(fams) >= 5:
        rho, p = spearmanr(fams, gaps)
        report["correlation"] = {"spearman_rho": round(float(rho), 3), "p_value": round(float(p), 3),
                                 "n_companies": len(fams),
                                 "what": "familiarity vs (neutral share named - neutral share anon)"}
        print(f"\nfamiliarity vs neutral gap (per company): Spearman rho={rho:.2f}, p={p:.2f}, n={len(fams)}")
        by_tier = defaultdict(list)
        for t, c in report["companies"].items():
            if c["familiarity"] is not None:
                by_tier[c["tier"]].append(c["familiarity"])
        print("mean familiarity by tier:", {t: round(mean(v), 1) for t, v in sorted(by_tier.items())})

    out = Path(sys.argv[1]).with_name(Path(sys.argv[1]).stem + "-compare.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nwritten: {out}")


if __name__ == "__main__":
    main()
