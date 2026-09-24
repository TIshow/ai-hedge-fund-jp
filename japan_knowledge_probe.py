"""Ask the agents' LLM what it already knows about each selected company.

Example:
    HEDGE_FUND_HOME=.hedge-fund .venv/bin/python japan_knowledge_probe.py \
        outputs/japan-universe-2026-02-09.json outputs/japan-universe-knowledge.json

One call per company, with the same model the agents use (HEDGE_FUND_LLM_MODEL
or the default). The self-rated familiarity (0-10) and the stated business
give a per-company measure of what a named prompt can let the model recall,
more direct than the TOPIX size tier. Re-running skips companies already in
the output file. Answers are model claims, not verified facts.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

SYSTEM = (
    "You are being asked what you already know from training, with no tools "
    "and no documents. Be honest about uncertainty; low familiarity is a "
    "perfectly good answer."
)

USER = """Company: {name_en} ({name}), Tokyo Stock Exchange code {ticker}.

What do you know about this company? Reply with JSON only:
{{"familiarity": <integer 0-10, 0 = never heard of it, 10 = know it in depth>,
  "business": "<one sentence on what it does, or 'unknown'>",
  "known_facts": ["<up to 5 short facts you are fairly confident about>"]}}"""


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    universe_path, out_path = Path(sys.argv[1]), Path(sys.argv[2])
    load_dotenv(".env", override=False)

    from hedge_fund.llm import extract_json, make_llm

    llm = make_llm()
    universe = json.loads(universe_path.read_text())
    results = json.loads(out_path.read_text()) if out_path.exists() else {}
    for company in universe["selected"]:
        ticker = company["ticker"]
        if ticker in results and "familiarity" in results[ticker]:
            continue
        user = USER.format(name_en=company.get("name_en") or company["name"],
                           name=company["name"], ticker=ticker)
        response = llm.complete(SYSTEM, user)
        entry = {"ticker": ticker, "tier": company["tier"], "model": llm.model,
                 "user": user, "response": response}
        try:
            parsed = extract_json(response)
            familiarity = int(parsed["familiarity"])
            if not 0 <= familiarity <= 10:
                raise ValueError(f"familiarity out of range: {familiarity}")
            entry.update(familiarity=familiarity, business=str(parsed.get("business", "")),
                         known_facts=list(parsed.get("known_facts", []))[:5])
        except (ValueError, KeyError, TypeError) as exc:
            entry["parse_error"] = str(exc)
        results[ticker] = entry
        out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2))
        print(f"  {company['tier']:<14} {ticker}  familiarity="
              f"{entry.get('familiarity', '?')}  {entry.get('business', '')[:60]}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
