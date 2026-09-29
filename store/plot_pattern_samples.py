"""Sample historical signal K-lines: 放量(current filter) vs 缩量(old scorer favorite)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Rectangle

for fp in (
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
):
    if Path(fp).exists():
        font_manager.fontManager.addfont(fp)
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=fp).get_name()
        break
plt.rcParams["axes.unicode_minus"] = False

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "store/pattern_samples"
OUT.mkdir(exist_ok=True)
KL = json.loads((ROOT / "store/backtest_klines.json").read_text())

SAMPLES = [
    ("放量·现行过滤", [
        ("HK.01489", "2026-08-06", "GC CONSTRUCTION"),
        ("HK.01710", "2026-09-02", "TRIO IND ELEC"),
        ("HK.09963", "2026-06-05", "高科桥"),
        ("HK.02450", "2026-08-27", "HUAIBEI GD"),
    ]),
    ("缩量·旧打分最强", [
        ("HK.01597", "2026-05-26", "纳泉能源科技"),
        ("HK.01536", "2026-09-23", "YUK WING GP"),
        ("HK.02028", "2026-09-23", "JOLIMARK"),
        ("HK.00199", "2026-05-26", "德祥地产"),
    ]),
]

conn = sqlite3.connect(ROOT / "store/signals.db")
meta = {}
for code, name, d, score, chg, vr in conn.execute(
    "SELECT code,name,scan_date,score,change_rate,vol_ratio FROM signals"
):
    meta[(code, d)] = (name or "", score, chg, vr)
conn.close()


def window(bars, scan, before=35, after=12):
    dates = [b["date"] for b in bars]
    if scan not in dates:
        return [], -1
    i = dates.index(scan)
    lo, hi = max(0, i - before), min(len(bars), i + after + 1)
    return bars[lo:hi], i - lo


def draw_one(ax_p, ax_v, bars, mark, title):
    w = 0.6
    for i, b in enumerate(bars):
        up = b["close"] >= b["open"]
        c = "#c0392b" if up else "#1e8449"
        lo, hi = min(b["open"], b["close"]), max(b["open"], b["close"])
        ax_p.plot([i, i], [b["low"], b["high"]], color=c, lw=0.8)
        ax_p.add_patch(Rectangle((i - w / 2, lo), w, max(hi - lo, 1e-6),
                                 facecolor=c, edgecolor=c, lw=0.4))
        ax_v.bar(i, b["volume"], color=c, width=0.7, alpha=0.75)
    if mark >= 0:
        ax_p.axvline(mark, color="#2980b9", ls="--", lw=1.1, alpha=0.85)
        ax_v.axvline(mark, color="#2980b9", ls="--", lw=1.1, alpha=0.85)
        if mark + 10 < len(bars):
            ax_p.axvspan(mark + 1, mark + 10, color="#2980b9", alpha=0.06)
    vols = [b["volume"] for b in bars]
    if vols:
        ma = [sum(vols[max(0, i - 19):i + 1]) / len(vols[max(0, i - 19):i + 1])
              for i in range(len(vols))]
        ax_v.plot(range(len(vols)), ma, color="#7f8c8d", lw=1.0)
    xs = [datetime.strptime(b["date"], "%Y-%m-%d") for b in bars]
    ax_p.set_title(title, fontsize=9, loc="left")
    ax_p.tick_params(axis="x", labelbottom=False)
    ticks = [0, mark if mark >= 0 else 0, len(xs) - 1]
    labels = [xs[0].strftime("%m-%d"),
              xs[mark].strftime("%m-%d") if 0 <= mark < len(xs) else "",
              xs[-1].strftime("%m-%d")]
    ax_v.set_xticks(ticks)
    ax_v.set_xticklabels(labels, fontsize=8)
    ax_v.tick_params(axis="y", labelsize=7)
    ax_p.tick_params(axis="y", labelsize=7)
    ax_p.grid(True, axis="y", alpha=0.25)
    ax_v.set_ylabel("Vol", fontsize=8)
    ax_p.set_ylabel("Px", fontsize=8)


fig = plt.figure(figsize=(15, 16))
fig.suptitle(
    "Left = current filter (vol 2-4)    Right = old scorer favorites (vol < 0.8)\n"
    "Blue dash = signal day    Shade = next 10 sessions    Grey = vol MA20",
    fontsize=12, y=0.995,
)
gs = fig.add_gridspec(8, 2, hspace=0.62, wspace=0.22, height_ratios=[2.1, 0.9] * 4)

for col, (col_title, items) in enumerate(SAMPLES):
    for row, (code, d, fallback) in enumerate(items):
        name, score, chg, vr = meta.get((code, d), (fallback, "?", 0, 0))
        bars = KL.get(code, [])
        win, mark = window(bars, d)
        ax_p = fig.add_subplot(gs[row * 2, col])
        ax_v = fig.add_subplot(gs[row * 2 + 1, col], sharex=ax_p)
        title = f"{code} {d}  vr={vr:.2f}  {chg:+.1f}%  score={score}"
        if not win:
            ax_p.text(0.5, 0.5, f"无K线 {code} {d}", ha="center", transform=ax_p.transAxes)
            continue
        draw_one(ax_p, ax_v, win, mark, title)

out = OUT / "pattern_grid.png"
fig.savefig(out, dpi=140, bbox_inches="tight", facecolor="white")
print("wrote", out)
