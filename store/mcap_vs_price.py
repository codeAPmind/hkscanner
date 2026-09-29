"""Compare 5d returns by market cap vs price. Snapshot only — no Futu kline quota."""
import json
import sqlite3
import statistics as st
from pathlib import Path

from moomoo import OpenQuoteContext, RET_OK

KL = json.loads(Path(__file__).with_name("backtest_klines.json").read_text())
conn = sqlite3.connect(Path(__file__).with_name("signals.db"))
sig = conn.execute(
    "SELECT code,name,scan_date,score,change_rate,vol_ratio FROM signals"
).fetchall()
conn.close()


def ret(a, b):
    if a is None or b is None or a <= 0:
        return None
    return b / a - 1


def sm(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    xs = sorted(xs)
    n = len(xs)
    return dict(n=n, mean=sum(xs) / n * 100, med=xs[n // 2] * 100,
                win=sum(1 for x in xs if x > 0) / n * 100)


def med(xs):
    xs = [x for x in xs if x is not None]
    return st.median(xs) if xs else None


trades = []
for code, name, d, score, chg, vr in sig:
    bars = KL.get(code)
    if not bars:
        continue
    im = {b["date"]: i for i, b in enumerate(bars)}
    if d not in im or im[d] + 1 >= len(bars):
        continue
    i = im[d]
    j = i + 5
    trades.append(dict(
        code=code, name=name, px=bars[i]["close"], vr=vr,
        r5=ret(bars[i + 1]["open"], bars[j]["close"]) if j < len(bars) else None,
    ))

codes = sorted({t["code"] for t in trades})
ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
mcap = {}
try:
    for i in range(0, len(codes), 200):
        batch = codes[i:i + 200]
        rc, data = ctx.get_market_snapshot(batch)
        if rc != RET_OK:
            print("snap fail", data)
            continue
        for _, r in data.iterrows():
            mv = r.get("total_market_val")
            if mv is not None and mv == mv and mv > 0:
                mcap[r["code"]] = float(mv)
finally:
    ctx.close()

for t in trades:
    t["mcap_b"] = mcap[t["code"]] / 1e8 if t["code"] in mcap else None

print("trades", len(trades), "mcap", len(mcap))

bins = [(0, 5, "<5亿"), (5, 20, "5-20亿"), (20, 50, "20-50亿"),
        (50, 100, "50-100亿"), (100, 300, "100-300亿"), (300, 1e9, ">=300亿")]
print("\n=== all by mcap ===")
for lo, hi, lab in bins:
    s = sm([t["r5"] for t in trades if t["mcap_b"] is not None and lo <= t["mcap_b"] < hi])
    if s:
        print(f"{lab:12} n={s['n']:4} mean={s['mean']:+6.2f}% med={s['med']:+6.2f}% win={s['win']:5.1f}%")

print("\n=== vr>=2 by mcap ===")
for lo, hi, lab in bins:
    s = sm([t["r5"] for t in trades if t["vr"] >= 2 and t["mcap_b"] is not None and lo <= t["mcap_b"] < hi])
    if s:
        print(f"{lab:12} n={s['n']:4} mean={s['mean']:+6.2f}% med={s['med']:+6.2f}% win={s['win']:5.1f}%")

print("\n=== comparisons ===")
preds = [
    ("vr>=2", lambda t: t["vr"] >= 2),
    ("vr>=2 px 0.5-10", lambda t: t["vr"] >= 2 and 0.5 <= t["px"] < 10),
    ("vr>=2 px>=10", lambda t: t["vr"] >= 2 and t["px"] >= 10),
    ("vr>=2 mcap<20亿", lambda t: t["vr"] >= 2 and t["mcap_b"] is not None and t["mcap_b"] < 20),
    ("vr>=2 mcap<50亿", lambda t: t["vr"] >= 2 and t["mcap_b"] is not None and t["mcap_b"] < 50),
    ("vr>=2 mcap<80亿", lambda t: t["vr"] >= 2 and t["mcap_b"] is not None and t["mcap_b"] < 80),
    ("vr>=2 5<=mcap<50", lambda t: t["vr"] >= 2 and t["mcap_b"] and 5 <= t["mcap_b"] < 50),
    ("vr>=2 mcap>=50亿", lambda t: t["vr"] >= 2 and t["mcap_b"] is not None and t["mcap_b"] >= 50),
    ("vr>=2 px>=10 & mcap<50", lambda t: t["vr"] >= 2 and t["px"] >= 10 and t["mcap_b"] and t["mcap_b"] < 50),
    ("vr>=2 px<10 & mcap>=50", lambda t: t["vr"] >= 2 and t["px"] < 10 and t["mcap_b"] and t["mcap_b"] >= 50),
    ("px>=10", lambda t: t["px"] >= 10),
    ("mcap>=50亿", lambda t: t["mcap_b"] is not None and t["mcap_b"] >= 50),
]
for lab, pred in preds:
    s = sm([t["r5"] for t in trades if pred(t)])
    print(f"{lab:28} " + (
        f"n={s['n']:4} mean={s['mean']:+6.2f}% med={s['med']:+6.2f}% win={s['win']:5.1f}%"
        if s else "none"))

print("\nmedian mcap亿 px<10", med([t["mcap_b"] for t in trades if t["px"] < 10]))
print("median mcap亿 px>=10", med([t["mcap_b"] for t in trades if t["px"] >= 10]))
print("median px mcap<20", med([t["px"] for t in trades if t["mcap_b"] and t["mcap_b"] < 20]))
print("median px mcap>=50", med([t["px"] for t in trades if t["mcap_b"] and t["mcap_b"] >= 50]))
print("px>=10 n", sum(1 for t in trades if t["px"] >= 10),
      "mcap<20", sum(1 for t in trades if t["px"] >= 10 and t["mcap_b"] and t["mcap_b"] < 20))
print("px<0.5 n", sum(1 for t in trades if t["px"] < 0.5),
      "median mcap", med([t["mcap_b"] for t in trades if t["px"] < 0.5]),
      "mcap>=20", sum(1 for t in trades if t["px"] < 0.5 and t["mcap_b"] and t["mcap_b"] >= 20))
