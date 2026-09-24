"""Render shareable PNGs and a GIF from the 30-stock named-vs-anonymized study.

Example:
    .venv/bin/python japan_media.py outputs/japan-30-named.json \
        outputs/japan-30-anon.json outputs/japan-universe-2026-02-16.json outputs/media

Reads only saved results (no API calls). Company names and tickers are not
shown; the one quoted example is labelled by industry and size tier only.
Every image carries the study's limits in its footer.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.animation import FuncAnimation, PillowWriter  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Rectangle  # noqa: E402

from japan_compare import verdicts  # noqa: E402

# Reference palette (dataviz skill), light mode; validated pairs:
# named/anon = categorical slots 1-2, bullish/bearish = diverging blue/red.
SURFACE, INK, INK_2, INK_3, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#8a8984", "#e4e3df"
NAMED, ANON = "#2a78d6", "#eb6834"
VIEW_COLOR = {"bullish": "#2a78d6", "neutral": "#d9d8d2", "bearish": "#e34948", "abstain": "#ffffff"}
VIEW_MARK = {"bullish": "▲", "neutral": "–", "bearish": "▼", "abstain": "?"}
VIEW_JA = {"bullish": "強気", "neutral": "中立", "bearish": "弱気", "abstain": "判断なし"}
VIEWS = ("bullish", "neutral", "bearish")
TIERS = ("TOPIX Core30", "TOPIX Large70", "TOPIX Mid400")
AGENT_JA = {"buffett": "Buffett役", "graham": "Graham役"}

FOOTER = ("予備的な結果: 会社単位では偶然を否定できない（Buffett役 11社 対 4社, p=0.12）。"
          "30社・2026/2/20–6/26の過去データによる研究用の検証で、投資助言ではありません。\n"
          "データ: J-Quants API（決算短信サマリー・日足） / AI: Claude Opus 5.5（上流 ai-hedge-fund の投資家プロンプト）")

plt.rcParams.update({
    "font.family": "Hiragino Sans", "font.size": 11, "text.color": INK,
    "axes.edgecolor": GRID, "axes.labelcolor": INK_2, "xtick.color": INK_2,
    "ytick.color": INK_2, "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
})


def _frame(fig, title: str, subtitle: str) -> None:
    fig.text(0.04, 0.955, title, fontsize=19, weight="bold", va="top")
    fig.text(0.04, 0.885, subtitle, fontsize=11.5, color=INK_2, va="top")
    fig.text(0.04, 0.025, FOOTER, fontsize=8.2, color=INK_3, va="bottom", linespacing=1.5)


def _pairs(named: dict, anon: dict) -> list[tuple[str, str, dict, dict]]:
    return [(k[0], k[1], named[k], anon[k]) for k in sorted(set(named) & set(anon))]


def _neutral_share(rows) -> float:
    return sum(r["view"] == "neutral" for r in rows) / len(rows)


def fig_neutral_share(pairs, tier_of, out: Path) -> None:
    fig = plt.figure(figsize=(16, 9), dpi=100)
    _frame(fig, "会社名を知ると、Buffett役のAIは「中立」に寄った",
           "同じ決算データに対する判断のうち「中立」の割合（30社・毎週の見直し19回・判断の組122）")
    left = fig.add_axes([0.06, 0.2, 0.4, 0.55])
    right = fig.add_axes([0.56, 0.2, 0.4, 0.55])
    w = 0.34

    def bars(ax, groups, labels):
        for i, rows in enumerate(groups):
            for j, (side, color, name) in enumerate(((2, NAMED, "会社名あり"), (3, ANON, "会社名なし"))):
                share = _neutral_share([r[side] for r in rows])
                x = i + (j - 0.5) * (w + 0.03)
                ax.bar(x, share * 100, w, color=color, label=name if i == 0 else None,
                       edgecolor=SURFACE, linewidth=2)
                ax.text(x, share * 100 + 1.5, f"{share:.0%}", ha="center", va="bottom",
                        fontsize=12, color=INK)
        ax.set_xticks(range(len(labels)), labels, fontsize=12)
        ax.set_ylim(0, 100)
        ax.set_yticks([0, 25, 50, 75, 100], ["0%", "25%", "50%", "75%", "100%"])
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        for s in ("top", "right", "left"):
            ax.spines[s].set_visible(False)
        ax.tick_params(length=0)

    agents = ("buffett", "graham")
    bars(left, [[p for p in pairs if p[0] == a] for a in agents], [AGENT_JA[a] for a in agents])
    left.set_title("判断役ごと", loc="left", fontsize=13, color=INK_2, pad=10)
    left.legend(loc="upper right", frameon=False, fontsize=11)
    buf = [p for p in pairs if p[0] == "buffett"]
    bars(right, [[p for p in buf if tier_of[p[1]] == t] for t in TIERS],
         [t.replace("TOPIX ", "") for t in TIERS])
    right.set_title("Buffett役・会社の規模別（Core30が最大手）", loc="left", fontsize=13, color=INK_2, pad=10)

    counts = {}
    for a in agents:
        rows = [p for p in pairs if p[0] == a]
        counts[a] = (sum(n["view"] == "neutral" != x["view"] for _, _, n, x in rows),
                     sum(x["view"] == "neutral" != n["view"] for _, _, n, x in rows))
    fig.text(0.06, 0.125,
             f"判断が食い違った組で「中立」だったのは… Buffett役: 会社名ありだけ {counts['buffett'][0]}件 / "
             f"なしだけ {counts['buffett'][1]}件　　Graham役: {counts['graham'][0]}件 / {counts['graham'][1]}件",
             fontsize=12, color=INK)
    fig.savefig(out)
    plt.close(fig)


def fig_transitions(pairs, out: Path) -> None:
    fig = plt.figure(figsize=(16, 9), dpi=100)
    _frame(fig, "判断はどう入れ替わったか",
           "行＝会社名ありの判断、列＝会社名なしの判断（同じ決算データ）。対角線上は同じ判断")
    blues = ["#f0efec", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95"]
    for k, agent in enumerate(("buffett", "graham")):
        ax = fig.add_axes([0.08 + k * 0.47, 0.19, 0.36, 0.54])
        c = Counter((n["view"], x["view"]) for a, _, n, x in pairs if a == agent)
        top = max(c.values())
        for i, rv in enumerate(VIEWS):
            for j, cv in enumerate(VIEWS):
                v = c.get((rv, cv), 0)
                shade = blues[0] if v == 0 else blues[min(6, 1 + int(5 * v / top))]
                ax.add_patch(Rectangle((j, 2 - i), 0.96, 0.96, facecolor=shade,
                                       edgecolor=INK if i == j else SURFACE, linewidth=1.6 if i == j else 2))
                ax.text(j + 0.48, 2 - i + 0.48, str(v), ha="center", va="center", fontsize=20,
                        color="#ffffff" if v / top > 0.55 else INK, weight="bold")
        ax.set_xlim(0, 3)
        ax.set_ylim(0, 3)
        ax.set_xticks([0.48, 1.48, 2.48], [f"{VIEW_MARK[v]} {VIEW_JA[v]}" for v in VIEWS], fontsize=12)
        ax.set_yticks([2.48, 1.48, 0.48], [f"{VIEW_MARK[v]} {VIEW_JA[v]}" for v in VIEWS], fontsize=12)
        ax.xaxis.set_ticks_position("top")
        ax.tick_params(length=0)
        for s in ax.spines.values():
            s.set_visible(False)
        total = sum(c.values())
        same = sum(v for (r, cc), v in c.items() if r == cc)
        ax.set_xlabel(f"会社名なし →　（同じ判断 {same}/{total}組）", fontsize=12, labelpad=10)
        ax.set_ylabel("会社名あり →", fontsize=12)
        ax.set_title(AGENT_JA[agent], loc="left", fontsize=15, weight="bold", pad=34)
    fig.text(0.08, 0.1, "強気と弱気が正反対になった組は、どちらの判断役でも0件。"
             "Buffett役の入れ替わりの多くは「会社名ありで中立 → なしで弱気」。", fontsize=12)
    fig.savefig(out)
    plt.close(fig)


def fig_design(out: Path, n_pairs: int) -> None:
    fig = plt.figure(figsize=(16, 9), dpi=100)
    _frame(fig, "実験の仕組み：同じ決算データを「会社名あり／なし」でAIに読ませる",
           "上流の virattt/ai-hedge-fund を日本株（J-Quants・100株単位）で動かし、判断だけを比べる")
    boxes = [
        ("① 銘柄をルールで選ぶ",
         "2026-02-16時点の上場銘柄一覧から\nプライム市場・金融業を除く\nTOPIX Core30 / Large70 / Mid400\nから各10社を無作為に\n（乱数の種を固定）\n\n人の好みや後知恵を入れない"),
        ("② 同じデータを2通りで渡す",
         "会社名あり：証券コード・業種・\n　時価総額・決算日つき（上流と同じ）\n会社名なし：それらを伏せ、\n　PER・ROE・利益率などの数字だけ\n\n財務は開示日の前日までの\n決算短信（直近12か月に換算）"),
        ("③ 判断を組にして比べる",
         f"Buffett役・Graham役が毎週判断\n同じデータへの判断を組にする\n（{n_pairs}組）\n\n仮説は結果を見る前に記録：\n会社名ありは「中立」が増える？\n有名企業ほど差が大きい？"),
    ]
    for i, (head, body) in enumerate(boxes):
        x = 0.05 + i * 0.315
        fig.patches.append(FancyBboxPatch((x, 0.24), 0.27, 0.54, boxstyle="round,pad=0.004,rounding_size=0.012",
                                          transform=fig.transFigure, facecolor="#ffffff",
                                          edgecolor=GRID, linewidth=1.5))
        fig.text(x + 0.018, 0.75, head, fontsize=18, weight="bold", va="top")
        fig.text(x + 0.018, 0.665, body, fontsize=15, va="top", linespacing=1.75, color=INK)
        if i < 2:
            fig.text(x + 0.2825, 0.51, "→", fontsize=26, color=INK_3, ha="center", va="center")
    fig.text(0.05, 0.16, "注意：AIは中型株もよく知っていた（自己評価 Core30 8.7 / Mid400 7.2 点、10点満点）。"
             "無料データには負債・流動比率がない。手数料・空売り費用は未考慮。", fontsize=11.5, color=INK_2)
    fig.savefig(out)
    plt.close(fig)


def fig_card(out: Path) -> None:
    """One Buffett pair, quoted (English original, figures elided)."""
    fig = plt.figure(figsize=(16, 9), dpi=100)
    _frame(fig, "同じ数字、違う結論",
           "ある大手製薬会社（Core30）・2026-02-20の見直し。Buffett役に渡した財務データは同一で、違いは会社名の有無だけ")
    named = ("“Reported returns are poor. ROE runs 1-2%, net margins are 1-3%, and revenue has fallen "
             "3-8% in recent periods … a P/E near 72 demands a recovery I can't see in the numbers. "
             "The cash tells a different story … That suggests heavy non-cash charges are depressing "
             "reported earnings. … I'll stay on the sidelines until the top line stabilizes.”")
    anon = ("“This business earns about 1-2% on equity with net margins of roughly 2-3%, and revenue "
            "has been shrinking for three straight periods. That is not a moat, it's a treadmill. "
            "At roughly 72 times earnings and about book value, I'm paying full price for a business "
            "that barely earns its cost of capital.”")
    import textwrap
    named_ja = ("要旨（訳）：報告上の収益力は低く、PER約72倍は数字に見えない回復が前提。"
                "だがキャッシュは別の姿を示す。非現金費用が利益を押し下げているのだろう。売上が落ち着くまで様子を見る。")
    anon_ja = ("要旨（訳）：ROE 1〜2%、純利益率2〜3%、売上は3期連続で減少。これは堀ではなく踏み車だ。"
               "約72倍の株価で、資本コストもろくに稼げない事業に満額を払うことになる。")
    for i, (label, color, view, conf, quote, note, gist) in enumerate((
        ("会社名あり", NAMED, "neutral", 45, named, "悪い数字を認めつつ、キャッシュを根拠に保留", named_ja),
        ("会社名なし", ANON, "bearish", 55, anon, "同じ数字から、はっきり弱気", anon_ja),
    )):
        x = 0.05 + i * 0.47
        fig.patches.append(FancyBboxPatch((x, 0.2), 0.43, 0.6, boxstyle="round,pad=0.004,rounding_size=0.012",
                                          transform=fig.transFigure, facecolor="#ffffff",
                                          edgecolor=GRID, linewidth=1.5))
        fig.patches.append(Rectangle((x + 0.012, 0.23), 0.006, 0.54, transform=fig.transFigure,
                                     facecolor=color, edgecolor="none"))
        fig.text(x + 0.03, 0.77, label, fontsize=15, weight="bold", va="top")
        fig.text(x + 0.03, 0.715, f"{VIEW_MARK[view]} {VIEW_JA[view]}（確信度 {conf}）", fontsize=17,
                 weight="bold", va="top", color=INK)
        fig.text(x + 0.03, 0.645, note, fontsize=12, color=INK_2, va="top")
        fig.text(x + 0.03, 0.585, textwrap.fill(quote, 58), fontsize=12.5, va="top",
                 linespacing=1.5, color=INK)
        fig.text(x + 0.03, 0.365, "\n".join(textwrap.wrap(gist, 30)), fontsize=12, va="top",
                 linespacing=1.55, color=INK_2)
    fig.text(0.05, 0.125, "引用はAIの回答（英語）の抜粋で、「…」は省略。要旨は筆者訳。会社名・株価は伏せています。",
             fontsize=11.5, color=INK_2)
    fig.savefig(out)
    plt.close(fig)


def weekly_gif(named_path: Path, anon_path: Path, universe: dict, out: Path) -> None:
    order = [c["ticker"] for t in TIERS for c in universe["selected"] if c["tier"] == t]

    def load(path):
        weeks: dict[str, dict[tuple[str, str], str]] = {}
        for rec in json.loads(path.read_text())["records"]:
            wk = weeks.setdefault(rec["as_of"], {})
            for st in rec["strategies"]:
                for s in st["signals"]:
                    m = s["metadata"]
                    wk[(s["model_name"], s["ticker"])] = "abstain" if m.get("abstained") else m["signal"]
        return weeks

    runs = {"named": load(named_path), "anon": load(anon_path)}
    dates = sorted(runs["named"])
    fig = plt.figure(figsize=(16, 9), dpi=100)
    _frame(fig, "毎週のAIの判断：会社名あり（左）と なし（右）",
           "30社（上から TOPIX Core30・Large70・Mid400 各10社、社名は伏せる）× 見直し19回。▲強気 –中立 ▼弱気")
    date_text = fig.text(0.96, 0.955, "", fontsize=15, ha="right", va="top", color=INK_2)
    cells, counters = {}, {}
    for r, agent in enumerate(("buffett", "graham")):
        for c, mode in enumerate(("named", "anon")):
            ax = fig.add_axes([0.1 + c * 0.45, 0.47 - r * 0.3, 0.39, 0.23])
            ax.set_xlim(0, 10)
            ax.set_ylim(0, 3)
            ax.axis("off")
            ax.set_title(f"{AGENT_JA[agent]}・{'会社名あり' if mode == 'named' else '会社名なし'}",
                         loc="left", fontsize=13, weight="bold", pad=4)
            counters[(agent, mode)] = ax.text(10, 3.35, "", ha="right", va="bottom", fontsize=11, color=INK_2)
            for i, ticker in enumerate(order):
                tier_row, col = divmod(i, 10)
                rect = Rectangle((col + 0.04, 2 - tier_row + 0.06), 0.92, 0.88, edgecolor=SURFACE, linewidth=2)
                ax.add_patch(rect)
                mark = ax.text(col + 0.5, 2 - tier_row + 0.5, "", ha="center", va="center", fontsize=11)
                cells[(agent, mode, ticker)] = (rect, mark)
            if c == 0:
                for tr, t in enumerate(TIERS):
                    ax.text(-0.2, 2 - tr + 0.5, t.replace("TOPIX ", ""), ha="right", va="center",
                            fontsize=10.5, color=INK_2)

    def draw(k):
        day = dates[min(k, len(dates) - 1)]
        date_text.set_text(f"{day}")
        for (agent, mode), counter in counters.items():
            views = Counter()
            for ticker in order:
                v = runs[mode][day].get((agent, ticker), "abstain")
                views[v] += 1
                rect, mark = cells[(agent, mode, ticker)]
                rect.set_facecolor(VIEW_COLOR[v])
                mark.set_text(VIEW_MARK[v])
                mark.set_color("#ffffff" if v in ("bullish", "bearish") else INK)
            counter.set_text(f"▲{views['bullish']}  –{views['neutral']}  ▼{views['bearish']}")
        return []

    frames = len(dates) + 4  # hold the last week
    anim = FuncAnimation(fig, draw, frames=frames, interval=800, blit=False)
    anim.save(out, writer=PillowWriter(fps=1.25))
    plt.close(fig)


def main() -> None:
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    named_path, anon_path, universe_path, out_dir = map(Path, sys.argv[1:])
    out_dir.mkdir(parents=True, exist_ok=True)
    universe = json.loads(universe_path.read_text())
    tier_of = {c["ticker"]: c["tier"] for c in universe["selected"]}
    pairs = _pairs(verdicts(named_path), verdicts(anon_path))
    fig_neutral_share(pairs, tier_of, out_dir / "01-neutral-share.png")
    fig_transitions(pairs, out_dir / "02-transitions.png")
    fig_design(out_dir / "03-design.png", len(pairs))
    fig_card(out_dir / "04-same-numbers-different-verdict.png")
    weekly_gif(named_path, anon_path, universe, out_dir / "05-weekly-views.gif")
    for f in sorted(out_dir.iterdir()):
        print(f"{f}  {f.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
