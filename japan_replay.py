"""Replay two saved fund backtests side by side in the terminal (for screen recording).

Example:
    .venv/bin/python japan_replay.py outputs/japan-30-named.json \
        outputs/japan-30-anon.json outputs/japan-universe-2026-02-16.json --speed 1.0

Reads only saved results: no API calls, no cost, re-runnable at will. Tickers
are replaced by size-tier aliases (Core30-1, Mid400-7, ...) so no company is
named. Size the terminal to about 170x48 before recording. --speed scales all
delays (2.0 = twice as fast); --final prints only the last week (no animation).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

TIERS = ("TOPIX Core30", "TOPIX Large70", "TOPIX Mid400")
NAMED_C, ANON_C, BENCH_C = "#2a78d6", "#eb6834", "#8a8984"
UP_C, DOWN_C, NEUTRAL_C = "#2a78d6", "#e34948", "#9d9c96"
VIEW = {"bullish": ("▲強気", UP_C), "neutral": ("–中立", NEUTRAL_C),
        "bearish": ("▼弱気", DOWN_C), "abstain": ("?なし", NEUTRAL_C)}
AGENT = {"buffett": "Buffett役", "graham": "Graham役"}
SPARK = "▁▂▃▄▅▆▇█"
FOOTER = ("社名は規模別の記号で伏せています ｜ 予備的な研究用の過去データ検証（2026/2/20–6/26・30社）で、投資助言ではありません ｜ "
          "手数料・税・配当・空売り費用は未考慮 ｜ データ: J-Quants API ｜ AI: Claude Opus 5.5")


def aliases(universe: dict) -> dict[str, str]:
    out = {}
    for tier in TIERS:
        members = [c["ticker"] for c in universe["selected"] if c["tier"] == tier]
        for i, ticker in enumerate(members, 1):
            out[ticker] = f"{tier.replace('TOPIX ', '')}-{i}"
    return out


def views(record: dict) -> dict[tuple[str, str], str]:
    out = {}
    for st in record["strategies"]:
        for s in st["signals"]:
            m = s["metadata"]
            out[(s["model_name"], s["ticker"])] = "abstain" if m.get("abstained") else m["signal"]
    return out


def spark(values: list[float], lo: float, hi: float, width: int) -> str:
    span = (hi - lo) or 1.0
    body = "".join(SPARK[min(7, int((v - lo) / span * 7.999))] for v in values)
    return body.ljust(width)


def area(values: list[float], bench: list[float], lo: float, hi: float, weeks: int,
         color: str, height: int = 7, colw: int = 4) -> Text:
    """Columns of eighth-blocks for *values*, with the benchmark as a dot."""
    span = (hi - lo) or 1.0
    cells = height * 8
    level = lambda v: max(1, round((v - lo) / span * (cells - 1)))  # noqa: E731
    out = Text()
    for row in range(height - 1, -1, -1):
        for w in range(weeks):
            if w >= len(values):
                out.append(" " * colw)
                continue
            f, b = level(values[w]), level(bench[w])
            filled = f - row * 8
            if filled >= 8:
                ch, style = "█", color
            elif filled > 0:
                ch, style = SPARK[filled - 1], color
            elif b // 8 == row:
                ch, style = "•", BENCH_C
            else:
                ch, style = " ", ""
            bench_here = b // 8 == row and filled < 8
            out.append(ch * (colw - 1), style=style)
            out.append("•" if bench_here and filled > 0 else " ", style=BENCH_C)
        out.append("\n")
    out.append("█", style=color)
    out.append(" 資産  ", style="dim")
    out.append("•", style=BENCH_C)
    out.append(" 1306（同じ資金で保有した場合）", style="dim")
    return out


def man(yen: float) -> str:
    return f"{yen / 1e4:,.0f}万円"


class Fund:
    def __init__(self, path: Path, label: str, color: str, alias: dict[str, str]):
        self.result = json.loads(path.read_text())
        self.label, self.color, self.alias = label, color, alias
        self.records = self.result["records"]
        self.capital = self.result["capital"]

    def panel(self, week: int, shown_orders: int, nav_now: float, lo: float, hi: float) -> Panel:
        rec = self.records[week]
        navs = [r["nav"] for r in self.records[: week + 1]]
        change = nav_now / self.capital - 1
        head = Text()
        head.append(f"{man(nav_now)}", style=f"bold {self.color}")
        head.append("   開始比 ", style="dim")
        head.append(f"{change:+.1%}", style=f"bold {UP_C if change >= 0 else DOWN_C}")
        bench = self.result["benchmark_nav"]
        line = area(navs[:-1] + [nav_now], bench, lo, hi, len(self.records), self.color)

        pos = rec["positions"]
        marks = rec["marks"]
        longs = {t: q for t, q in pos.items() if q > 0}
        shorts = {t: q for t, q in pos.items() if q < 0}
        book = Text()
        book.append(f"買い {len(longs)}銘柄 ", style=UP_C)
        book.append(f"{man(sum(q * marks[t] for t, q in longs.items()))}", style="bold")
        book.append("   ")
        book.append(f"空売り {len(shorts)}銘柄 ", style=DOWN_C)
        book.append(f"{man(-sum(q * marks[t] for t, q in shorts.items()))}", style="bold")
        book.append(f"   現金 {man(rec['cash'])}", style="dim")

        top = Table(box=None, show_header=True, header_style="dim", padding=(0, 1), expand=True)
        top.add_column("大きい持ち高", ratio=3)
        top.add_column("株数", justify="right", ratio=2)
        top.add_column("評価額", justify="right", ratio=2)
        top.add_column("比率", justify="right", ratio=1)
        ranked = sorted(pos.items(), key=lambda kv: -abs(kv[1] * marks[kv[0]]))[:6]
        for t, q in ranked:
            value = q * marks[t]
            color = UP_C if q > 0 else DOWN_C
            top.add_row(Text(("▲ " if q > 0 else "▼ ") + self.alias[t], style=color),
                        f"{q:+,}", man(value), f"{value / rec['nav']:+.1%}")
        for _ in range(6 - len(ranked)):
            top.add_row("", "", "", "")

        orders = Text()
        todo = rec["orders"]
        orders.append(f"今週の注文 {len(todo)}件\n", style="dim")
        for o in todo[:shown_orders][-7:]:
            buy = o["side"] == "buy"
            orders.append(("買 " if buy else "売 "), style=f"bold {UP_C if buy else DOWN_C}")
            orders.append(f"{self.alias[o['ticker']]:<11}{o['quantity']:>6,}株  {man(o['quantity'] * o['price']):>9}\n")
        hidden = min(shown_orders, len(todo)) - 7
        orders.append(f"…ほか{hidden}件\n" if hidden > 0 else "\n", style="dim")

        changes = Text()
        now = views(rec)
        if week == 0:
            for agent in AGENT:
                counts = {v: sum(1 for (a, _), x in now.items() if a == agent and x == v) for v in VIEW}
                changes.append(f"{AGENT[agent]} 最初の判断: ", style="dim")
                for v in ("bullish", "neutral", "bearish"):
                    changes.append(f"{VIEW[v][0]}{counts[v]} ", style=VIEW[v][1])
                changes.append("\n")
        else:
            before = views(self.records[week - 1])
            flips = [(a, t) for (a, t), v in now.items() if before.get((a, t)) != v]
            changes.append(f"AIの判断が変わった銘柄 {len(flips)}件\n", style="dim")
            for a, t in sorted(flips, key=lambda k: (k[0], self.alias[k[1]]))[:4]:
                old, new = VIEW[before.get((a, t), "abstain")], VIEW[now[(a, t)]]
                changes.append(f"{AGENT[a]} {self.alias[t]:<11}")
                changes.append(old[0], style=old[1])
                changes.append(" → ")
                changes.append(new[0] + "\n", style=f"bold {new[1]}")
            if len(flips) > 4:
                changes.append(f"…ほか{len(flips) - 4}件\n", style="dim")

        body = Group(head, Text(""), line, Text(""), book, Text(""), top, Text(""), orders, changes)
        return Panel(body, title=f"[bold {self.color}]{self.label}[/]", border_style=self.color,
                     padding=(0, 1))


def frame(funds, week: int, shown: int, navs_now, bench, lo, hi, weeks: int, dates) -> Layout:
    layout = Layout()
    layout.split_column(Layout(name="head", size=4), Layout(name="body"), Layout(name="foot", size=4))
    b_now = bench[week]
    title = Text()
    title.append("AIファンド リプレイ：会社名あり vs 会社名なし", style="bold")
    title.append(f"    {dates[week]}  （{week + 1}/{weeks}週）", style="bold")
    sub = Text()
    sub.append("Buffett役＋Graham役・日本株30社・資金2億円・100株単位・空売りあり    ", style="dim")
    sub.append("比較: 1306（TOPIX連動ETF） ", style="dim")
    sub.append(f"{b_now / funds[0].capital - 1:+.1%} ", style=f"bold {BENCH_C}")
    sub.append(spark(bench[: week + 1], lo, hi, weeks), style=BENCH_C)
    layout["head"].update(Panel(Group(title, sub), border_style="dim"))
    layout["body"].split_row(*(Layout(f.panel(week, shown, n, lo, hi)) for f, n in zip(funds, navs_now)))
    layout["foot"].update(Panel(Text(FOOTER, style="dim"), border_style="dim"))
    return layout


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("named")
    ap.add_argument("anon")
    ap.add_argument("universe")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--final", action="store_true", help="print the last week only")
    args = ap.parse_args()

    alias = aliases(json.loads(Path(args.universe).read_text()))
    funds = [Fund(Path(args.named), "会社名あり（上流と同じ入力）", NAMED_C, alias),
             Fund(Path(args.anon), "会社名なし（数字だけ）", ANON_C, alias)]
    dates = funds[0].result["dates"]
    assert dates == funds[1].result["dates"], "the two runs must share rebalance dates"
    bench = funds[0].result["benchmark_nav"]
    series = [r["nav"] for f in funds for r in f.records] + bench
    lo, hi = min(series), max(series)
    weeks = len(dates)
    console = Console()
    pause = lambda s: time.sleep(s / max(args.speed, 0.01))  # noqa: E731

    if args.final:
        w = weeks - 1
        console.print(frame(funds, w, 10**6, [f.records[w]["nav"] for f in funds], bench, lo, hi, weeks, dates),
                      height=console.height)
        return

    with Live(console=console, screen=True, auto_refresh=False) as live:
        prev = [f.capital for f in funds]
        for w in range(weeks):
            new = [f.records[w]["nav"] for f in funds]
            most = max(len(f.records[w]["orders"]) for f in funds)
            for k in range(1, min(most, 12) + 1):  # stream the week's orders
                live.update(frame(funds, w, k, prev, bench, lo, hi, weeks, dates), refresh=True)
                pause(0.12)
            steps = 8  # count the NAV up or down to the new close
            for s in range(1, steps + 1):
                now = [p + (n - p) * s / steps for p, n in zip(prev, new)]
                live.update(frame(funds, w, 10**6, now, bench, lo, hi, weeks, dates), refresh=True)
                pause(0.05)
            prev = new
            pause(1.4)
        pause(4.0)


if __name__ == "__main__":
    main()
