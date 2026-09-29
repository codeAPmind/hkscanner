"""Evaluate historical hkscanner signals with Yahoo daily bars."""
from __future__ import annotations

import json
import sqlite3
import statistics as st
from collections import defaultdict
from pathlib import Path

KL = json.loads(Path("/Users/openclaw/openclaw_workspace/hkscanner/store/backtest_klines.json").read_text())
conn = sqlite3.connect("/Users/openclaw/openclaw_workspace/hkscanner/store/signals.db")
sig = conn.execute(
    "SELECT id,code,name,scan_date,score,stage,change_rate,vol_ratio,ai_summary "
    "FROM signals ORDER BY scan_date, id"
).fetchall()
conn.close()

HORIZONS = [1, 3, 5, 10]


def idx_map(bars):
    return {b["date"]: i for i, b in enumerate(bars)}


def ret(a, b):
    if a is None or b is None or a <= 0:
        return None
    return (b / a) - 1.0


def summarize(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    xs = sorted(xs)
    n = len(xs)
    mean = sum(xs) / n
    med = xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])
    wins = [x for x in xs if x > 0]
    loss = [x for x in xs if x <= 0]
    p25 = xs[int((n - 1) * 0.25)]
    p75 = xs[int((n - 1) * 0.75)]
    return {
        "n": n,
        "mean": mean,
        "median": med,
        "win_rate": len(wins) / n,
        "p25": p25,
        "p75": p75,
        "avg_win": sum(wins) / len(wins) if wins else 0,
        "avg_loss": sum(loss) / len(loss) if loss else 0,
        "best": xs[-1],
        "worst": xs[0],
    }


hsi = KL.get("^HSI", [])
hsi_i = idx_map(hsi)

trades = []
missing_px = 0
for sid, code, name, d, score, stage, chg, vr, ai in sig:
    bars = KL.get(code)
    if not bars:
        missing_px += 1
        continue
    im = idx_map(bars)
    if d not in im:
        missing_px += 1
        continue
    i = im[d]
    t_close = bars[i]["close"]
    t_open = bars[i]["open"]
    # next-day open entry (scan is after close)
    if i + 1 >= len(bars):
        continue
    entry = bars[i + 1]["open"]
    if entry <= 0 or t_close <= 0:
        continue
    gap = ret(t_close, entry)
    rec = {
        "id": sid,
        "code": code,
        "name": name or code,
        "date": d,
        "score": score,
        "stage": (stage or "").split()[0],
        "chg": chg,
        "vr": vr,
        "t_close": t_close,
        "entry": entry,
        "gap": gap,
        "penny": t_close < 0.5,
        "extreme_day": chg >= 30,
    }
    for h in HORIZONS:
        j = i + 1 + (h - 1)  # close of entry day is h=1; close of T+h
        # h=1: close of T+1 (same day as entry)
        # h=3: close of T+3
        if j < len(bars):
            rec[f"r{h}"] = ret(entry, bars[j]["close"])
            rec[f"mfe{h}"] = ret(entry, max(b["high"] for b in bars[i + 1 : j + 1]))
            rec[f"mae{h}"] = ret(entry, min(b["low"] for b in bars[i + 1 : j + 1]))
        else:
            rec[f"r{h}"] = rec[f"mfe{h}"] = rec[f"mae{h}"] = None
        if d in hsi_i:
            hi = hsi_i[d]
            hj = hi + 1 + (h - 1)
            if hj < len(hsi) and hsi[hi]["close"] > 0:
                rec[f"hsi{h}"] = ret(hsi[hi]["close"], hsi[hj]["close"])
            else:
                rec[f"hsi{h}"] = None
        else:
            rec[f"hsi{h}"] = None
    trades.append(rec)

# first-seen only (avoid riding same name across consecutive days)
first = []
seen = set()
for t in trades:
    k = t["code"]
    if k in seen:
        continue
    seen.add(k)
    first.append(t)

# first appearance in a 5-day window (matches is_new lookback=3-ish, use 5)
fresh = []
last = {}
for t in trades:
    prev = last.get(t["code"])
    last[t["code"]] = t["date"]
    if prev is None:
        t = dict(t)
        t["fresh"] = True
        fresh.append(t)
        continue
    # trading-day gap via date string compare is enough for 5 calendar days
    from datetime import datetime
    gap_d = (datetime.strptime(t["date"], "%Y-%m-%d") - datetime.strptime(prev, "%Y-%m-%d")).days
    t = dict(t)
    t["fresh"] = gap_d >= 5
    if t["fresh"]:
        fresh.append(t)

# monthly
by_month = defaultdict(list)
for t in trades:
    by_month[t["date"][:7]].append(t)

# daily equal-weight book
by_day = defaultdict(list)
for t in trades:
    by_day[t["date"]].append(t)
daily_r5 = []
for d in sorted(by_day):
    xs = [t["r5"] for t in by_day[d] if t["r5"] is not None]
    if xs:
        daily_r5.append({"date": d, "mean": sum(xs) / len(xs), "n": len(xs)})

# score vs return (5d)
score_bins = [
    ("60-69", 60, 70),
    ("70-79", 70, 80),
    ("80-89", 80, 90),
    ("90-100", 90, 101),
]
vr_bins = [
    ("<0.8 极度缩量", 0, 0.8),
    ("0.8-1.2 温和", 0.8, 1.2),
    ("1.2-2.0 正常", 1.2, 2.0),
    (">=2.0 放量", 2.0, 99),
]
chg_bins = [
    ("8-12% 刚过门槛", 8, 12),
    ("12-20% 大阳", 12, 20),
    ("20-30% 主升", 20, 30),
    (">=30% 极端", 30, 999),
]
px_bins = [
    ("<0.5 仙股", 0, 0.5),
    ("0.5-2", 0.5, 2),
    ("2-10", 2, 10),
    (">=10", 10, 1e9),
]


def bucket(trades, key_fn, bins, field="r5"):
    out = []
    for label, lo, hi in bins:
        xs = [t[field] for t in trades if t[field] is not None and lo <= key_fn(t) < hi]
        s = summarize(xs)
        if s:
            s["label"] = label
            out.append(s)
    return out


def stage_bucket(trades, field="r5"):
    out = []
    for stg in ["反转启动", "拉升确认", "主升浪加速"]:
        xs = [t[field] for t in trades if t[field] is not None and t["stage"] == stg]
        s = summarize(xs)
        if s:
            s["label"] = stg
            out.append(s)
    return out


# hypothetical filters
def filt(pred, field="r5"):
    return summarize([t[field] for t in trades if pred(t) and t[field] is not None])


scenarios = {
    "all_T1open_5d": summarize([t["r5"] for t in trades]),
    "all_T1open_1d": summarize([t["r1"] for t in trades]),
    "all_T1open_3d": summarize([t["r3"] for t in trades]),
    "all_T1open_10d": summarize([t["r10"] for t in trades]),
    "first_seen_5d": summarize([t["r5"] for t in first]),
    "fresh_5d": summarize([t["r5"] for t in fresh]),
    "no_penny_5d": filt(lambda t: not t["penny"]),
    "no_extreme_5d": filt(lambda t: not t["extreme_day"]),
    "quality_5d": filt(lambda t: not t["penny"] and t["chg"] < 30 and t["vr"] < 1.2 and t["score"] >= 75),
    "score_ge_80": filt(lambda t: t["score"] >= 80),
    "score_ge_90": filt(lambda t: t["score"] >= 90),
    "vr_lt_0.8": filt(lambda t: t["vr"] < 0.8),
    "vr_lt_1.2": filt(lambda t: t["vr"] < 1.2),
    "chg_8_15": filt(lambda t: 8 <= t["chg"] < 15),
    "price_ge_0.5": filt(lambda t: t["t_close"] >= 0.5),
    "price_ge_2": filt(lambda t: t["t_close"] >= 2),
    "top3_per_day_5d": summarize([
        t["r5"] for d, xs in by_day.items()
        for t in sorted(xs, key=lambda z: z["score"], reverse=True)[:3]
        if t["r5"] is not None
    ]),
    "hsi_5d": summarize([t["hsi5"] for t in trades]),
    "gap": summarize([t["gap"] for t in trades if t["gap"] is not None]),
}

# monthly series
monthly = []
for m in sorted(by_month):
    s = summarize([t["r5"] for t in by_month[m]])
    hs = summarize([t["hsi5"] for t in by_month[m]])
    if s:
        monthly.append({
            "month": m,
            "n": s["n"],
            "mean": s["mean"],
            "median": s["median"],
            "win_rate": s["win_rate"],
            "hsi": hs["mean"] if hs else None,
        })

# worst / best 5d
ranked = [t for t in trades if t["r5"] is not None]
best10 = sorted(ranked, key=lambda t: t["r5"], reverse=True)[:10]
worst10 = sorted(ranked, key=lambda t: t["r5"])[:10]

# repeat offenders
from collections import Counter
cnt = Counter(t["code"] for t in trades)
repeat_codes = [c for c, n in cnt.most_common(12)]

# score correlation-ish: mean r5 by score
score_curve = []
for lo in range(60, 101, 5):
    xs = [t["r5"] for t in trades if t["r5"] is not None and lo <= t["score"] < lo + 5]
    if len(xs) >= 8:
        score_curve.append({"score": f"{lo}-{lo+4}", "mean": sum(xs) / len(xs), "n": len(xs),
                            "win": sum(1 for x in xs if x > 0) / len(xs)})

# daily equity of equal-weight 5d overlapping is messy; instead cumulative
# of daily-book 5d mean (not a true equity, but trend)
cum = []
acc = 0
for row in daily_r5:
    acc += row["mean"]
    cum.append({"date": row["date"], "cum": acc, "n": row["n"]})

# coverage
dates = sorted({t["date"] for t in trades})

out = {
    "meta": {
        "signals_raw": len(sig),
        "trades": len(trades),
        "missing_px": missing_px,
        "unique_codes": len({t["code"] for t in trades}),
        "dates": len(dates),
        "date_start": dates[0] if dates else None,
        "date_end": dates[-1] if dates else None,
        "entry": "T+1 open after 18:20 after-close scan",
        "source": "yfinance daily, 2026-05-01 ~ 2026-09-24",
        "missing_yahoo": ["HK.02958", "HK.08603"],
    },
    "scenarios": scenarios,
    "score_bins": bucket(trades, lambda t: t["score"], score_bins),
    "vr_bins": bucket(trades, lambda t: t["vr"], vr_bins),
    "chg_bins": bucket(trades, lambda t: t["chg"], chg_bins),
    "px_bins": bucket(trades, lambda t: t["t_close"], px_bins),
    "stage_bins": stage_bucket(trades),
    "monthly": monthly,
    "daily_cum": cum,
    "score_curve": score_curve,
    "best10": [{k: t[k] for k in ("date", "code", "name", "score", "chg", "vr", "t_close", "r5")} for t in best10],
    "worst10": [{k: t[k] for k in ("date", "code", "name", "score", "chg", "vr", "t_close", "r5")} for t in worst10],
    "repeat_top": [{"code": c, "n": cnt[c],
                    "mean5": summarize([t["r5"] for t in trades if t["code"] == c])["mean"]
                    if summarize([t["r5"] for t in trades if t["code"] == c]) else None}
                   for c in repeat_codes],
}

# print readable
def pct(x):
    return "—" if x is None else f"{x*100:+.2f}%"

print(f"trades {out['meta']['trades']} / raw {out['meta']['signals_raw']}  missing {missing_px}")
print(f"range {out['meta']['date_start']} -> {out['meta']['date_end']}  days {out['meta']['dates']}")
print()
for k, s in scenarios.items():
    if not s:
        print(f"{k}: none")
        continue
    print(f"{k:22} n={s['n']:4}  mean={pct(s['mean']):>8}  med={pct(s['median']):>8}  win={s['win_rate']*100:5.1f}%  p25={pct(s['p25'])}  p75={pct(s['p75'])}")

print("\nscore bins")
for s in out["score_bins"]:
    print(f"  {s['label']:10} n={s['n']:4} mean={pct(s['mean'])} med={pct(s['median'])} win={s['win_rate']*100:.1f}%")
print("\nvr bins")
for s in out["vr_bins"]:
    print(f"  {s['label']:16} n={s['n']:4} mean={pct(s['mean'])} med={pct(s['median'])} win={s['win_rate']*100:.1f}%")
print("\nchg bins")
for s in out["chg_bins"]:
    print(f"  {s['label']:16} n={s['n']:4} mean={pct(s['mean'])} med={pct(s['median'])} win={s['win_rate']*100:.1f}%")
print("\npx bins")
for s in out["px_bins"]:
    print(f"  {s['label']:12} n={s['n']:4} mean={pct(s['mean'])} med={pct(s['median'])} win={s['win_rate']*100:.1f}%")
print("\nstage")
for s in out["stage_bins"]:
    print(f"  {s['label']:10} n={s['n']:4} mean={pct(s['mean'])} med={pct(s['median'])} win={s['win_rate']*100:.1f}%")
print("\nmonthly")
for m in monthly:
    print(f"  {m['month']} n={m['n']:3} mean={pct(m['mean'])} med={pct(m['median'])} win={m['win_rate']*100:.1f}%  hsi={pct(m['hsi'])}")

Path("/Users/openclaw/openclaw_workspace/hkscanner/store/backtest_stats.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2)
)
print("\nwrote store/backtest_stats.json")
