"""Stress-test 10-day hold on the 20-50亿 + vr>=2 slice vs wider sets."""
import json
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from moomoo import OpenQuoteContext, RET_OK

KL = json.loads(Path(__file__).with_name("backtest_klines.json").read_text())
conn = sqlite3.connect(Path(__file__).with_name("signals.db"))
sig = conn.execute(
    "SELECT code, name, scan_date, score, change_rate, vol_ratio FROM signals"
).fetchall()
conn.close()


def ret(a, b):
    if not a or not b or a <= 0:
        return None
    return b / a - 1


def sm(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    xs = sorted(xs)
    n = len(xs)
    return dict(n=n, mean=sum(xs) / n, med=xs[n // 2],
                win=sum(1 for x in xs if x > 0) / n,
                p25=xs[int((n - 1) * 0.25)], p75=xs[int((n - 1) * 0.75)],
                best=xs[-1], worst=xs[0])


def pct(x):
    return "—" if x is None else f"{x * 100:+.1f}%"


raw = []
for code, name, d, score, chg, vr in sig:
    bars = KL.get(code)
    if not bars:
        continue
    im = {b["date"]: i for i, b in enumerate(bars)}
    if d not in im or im[d] + 1 >= len(bars):
        continue
    i = im[d]
    entry = bars[i + 1]["open"]
    if entry <= 0:
        continue
    rec = dict(code=code, name=name or code, date=d, score=score, chg=chg,
               vr=vr, i=i, bars=bars, entry=entry, px=bars[i]["close"])
    for h in (3, 5, 10):
        j = i + 1 + (h - 1)
        if j < len(bars):
            rec[f"r{h}"] = ret(entry, bars[j]["close"])
            rec[f"mfe{h}"] = ret(entry, max(b["high"] for b in bars[i + 1:j + 1]))
            rec[f"mae{h}"] = ret(entry, min(b["low"] for b in bars[i + 1:j + 1]))
        else:
            rec[f"r{h}"] = rec[f"mfe{h}"] = rec[f"mae{h}"] = None
    raw.append(rec)

codes = sorted({t["code"] for t in raw})
ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
mcap = {}
try:
    for i in range(0, len(codes), 200):
        rc, data = ctx.get_market_snapshot(codes[i:i + 200])
        if rc != RET_OK:
            continue
        for _, r in data.iterrows():
            mv = r.get("total_market_val")
            if mv is not None and mv == mv and mv > 0:
                mcap[r["code"]] = float(mv)
finally:
    ctx.close()

for t in raw:
    t["mcap_b"] = mcap[t["code"]] / 1e8 if t["code"] in mcap else None

core = [t for t in raw if t["vr"] >= 2 and t["mcap_b"] and 20 <= t["mcap_b"] < 50 and t["r10"] is not None]
wide = [t for t in raw if t["vr"] >= 2 and t["mcap_b"] and 5 <= t["mcap_b"] < 50 and t["r10"] is not None]
allv = [t for t in raw if t["vr"] >= 2 and t["r10"] is not None]

print("=== 10d headline ===")
for lab, xs in (("core 20-50", core), ("wide 5-50", wide), ("all vr>=2", allv)):
    s = sm([t["r10"] for t in xs])
    print(f"{lab:14} n={s['n']:3} mean={pct(s['mean'])} med={pct(s['med'])} "
          f"win={s['win']*100:.1f}% p25={pct(s['p25'])} p75={pct(s['p75'])} "
          f"best={pct(s['best'])} worst={pct(s['worst'])}")

print("\n=== core 10d each trade, sorted by r10 ===")
for t in sorted(core, key=lambda x: x["r10"], reverse=True):
    print(f"{t['date']} {t['code']} {t['name'][:16]:16} "
          f"mcap={t['mcap_b']:5.1f} vr={t['vr']:.2f} chg={t['chg']:5.1f} "
          f"r5={pct(t['r5']):>7} r10={pct(t['r10']):>7} "
          f"mfe10={pct(t['mfe10']):>7} mae10={pct(t['mae10']):>7}")

# concentration
xs = sorted([t["r10"] for t in core], reverse=True)
print("\n=== concentration ===")
print(f"sum of all r10 {sum(xs)*100:.1f}%   top1 {xs[0]*100:.1f}%  "
      f"top3 {sum(xs[:3])*100:.1f}%  share top3 {sum(xs[:3])/sum(xs)*100:.0f}%")
print(f"mean drop top1 {sum(xs[1:])/len(xs[1:])*100:+.1f}%  "
      f"mean drop top3 {sum(xs[3:])/len(xs[3:])*100:+.1f}%")
s_wo = sm(xs[3:])
print(f"without top3: n={s_wo['n']} mean={pct(s_wo['mean'])} med={pct(s_wo['med'])} win={s_wo['win']*100:.1f}%")

print("\n=== paired 5d vs 10d on same 26 ===")
better10 = sum(1 for t in core if t["r10"] is not None and t["r5"] is not None and t["r10"] > t["r5"])
worse10 = sum(1 for t in core if t["r10"] is not None and t["r5"] is not None and t["r10"] < t["r5"])
print(f"10d > 5d: {better10}   10d < 5d: {worse10}")
delta = [t["r10"] - t["r5"] for t in core if t["r10"] is not None and t["r5"] is not None]
print("extra return 5->10", pct(sum(delta) / len(delta)), "med", pct(sorted(delta)[len(delta)//2]))

print("\n=== by month core ===")
by_m = defaultdict(list)
for t in core:
    by_m[t["date"][:7]].append(t)
for m in sorted(by_m):
    s5 = sm([t["r5"] for t in by_m[m]])
    s10 = sm([t["r10"] for t in by_m[m]])
    print(f"{m} n={len(by_m[m]):2}  5d {pct(s5['mean'])}/{pct(s5['med'])}/{s5['win']*100:.0f}%   "
          f"10d {pct(s10['mean'])}/{pct(s10['med'])}/{s10['win']*100:.0f}%")

print("\n=== underwater: max adverse in 10d ===")
deep = sum(1 for t in core if t["mae10"] is not None and t["mae10"] <= -0.10)
print(f"MAE<=-10% during 10d: {deep}/{len(core)}")
# ended green but was -10% first
gave = [t for t in core if t["mae10"] and t["mae10"] <= -0.10 and t["r10"] and t["r10"] > 0]
print(f"ended green after MAE<=-10%: {len(gave)}")
for t in gave:
    print(f"  {t['date']} {t['code']} {t['name']} mae={pct(t['mae10'])} r10={pct(t['r10'])}")

print("\n=== overlap if hold 10 calendar trading days ===")
# approximate concurrent positions: each signal occupies next 10 bars
# use signal dates as timeline
all_days = sorted({b["date"] for t in core for b in t["bars"]})
day_i = {d: i for i, d in enumerate(all_days)}
occ = defaultdict(int)
for t in core:
    if t["date"] not in day_i:
        continue
    start = day_i[t["date"]] + 1
    for k in range(start, min(start + 10, len(all_days))):
        occ[all_days[k]] += 1
if occ:
    xs = list(occ.values())
    print(f"concurrent on occupied days: mean={sum(xs)/len(xs):.2f} max={max(xs)}")
    print("days with 3+ names", sum(1 for v in xs if v >= 3), "/", len(xs))

print("\n=== first-only vs all repeats (same name, 10d) ===")
seen = set()
first = []
for t in sorted(core, key=lambda x: x["date"]):
    if t["code"] in seen:
        continue
    seen.add(t["code"])
    first.append(t)
print("first-only", sm([t["r10"] for t in first]))
print("all", sm([t["r10"] for t in core]))

print("\n=== leave-one-month-out 10d mean ===")
for m in sorted(by_m):
    xs = [t["r10"] for t in core if t["date"][:7] != m]
    s = sm(xs)
    print(f"drop {m}: n={s['n']} mean={pct(s['mean'])} med={pct(s['med'])} win={s['win']*100:.1f}%")
