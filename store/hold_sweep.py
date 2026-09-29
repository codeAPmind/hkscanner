"""Hold-period sweep for the 20-50亿 + vol>=2 slice. Snapshot only, no kline quota."""
import json
import sqlite3
from pathlib import Path

from moomoo import OpenQuoteContext, RET_OK

KL = json.loads(Path(__file__).with_name("backtest_klines.json").read_text())
conn = sqlite3.connect(Path(__file__).with_name("signals.db"))
sig = conn.execute(
    "SELECT code, name, scan_date, change_rate, vol_ratio FROM signals"
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
    wins = [x for x in xs if x > 0]
    loss = [x for x in xs if x <= 0]
    return {
        "n": n,
        "mean": sum(xs) / n,
        "med": xs[n // 2],
        "win": sum(1 for x in xs if x > 0) / n,
        "p25": xs[int((n - 1) * 0.25)],
        "p75": xs[int((n - 1) * 0.75)],
        "avg_win": sum(wins) / len(wins) if wins else 0,
        "avg_loss": sum(loss) / len(loss) if loss else 0,
        "best": xs[-1],
        "worst": xs[0],
    }


trades_raw = []
for code, name, d, chg, vr in sig:
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
    trades_raw.append(dict(code=code, name=name, date=d, vr=vr, chg=chg,
                           i=i, bars=bars, entry=entry))

codes = sorted({t["code"] for t in trades_raw})
ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
mcap = {}
try:
    for i in range(0, len(codes), 200):
        rc, data = ctx.get_market_snapshot(codes[i:i + 200])
        if rc != RET_OK:
            print("snap fail", data)
            continue
        for _, r in data.iterrows():
            mv = r.get("total_market_val")
            if mv is not None and mv == mv and mv > 0:
                mcap[r["code"]] = float(mv)
finally:
    ctx.close()

core = []
for t in trades_raw:
    mv = mcap.get(t["code"])
    if mv is None:
        continue
    t = dict(t)
    t["mcap_b"] = mv / 1e8
    if t["vr"] >= 2 and 20 <= t["mcap_b"] < 50:
        core.append(t)

print(f"core n={len(core)} unique={len({t['code'] for t in core})} "
      f"days={len({t['date'] for t in core})}")

print("\n=== hold T+h close vs T+1 open  (20-50亿 & vr>=2) ===")
print(f"{'h':>3} {'n':>4} {'mean':>8} {'med':>8} {'win':>7} {'p25':>8} {'p75':>8} "
      f"{'avgW':>8} {'avgL':>8} {'mfe':>8} {'mae':>8}")
for h in range(1, 16):
    r, mfe, mae = [], [], []
    for t in core:
        j = t["i"] + 1 + (h - 1)
        if j >= len(t["bars"]):
            continue
        window = t["bars"][t["i"] + 1:j + 1]
        r.append(ret(t["entry"], t["bars"][j]["close"]))
        mfe.append(ret(t["entry"], max(b["high"] for b in window)))
        mae.append(ret(t["entry"], min(b["low"] for b in window)))
    s = sm(r)
    fe, ae = sm(mfe), sm(mae)
    if not s:
        continue
    print(f"{h:3} {s['n']:4} {s['mean']*100:+7.2f}% {s['med']*100:+7.2f}% "
          f"{s['win']*100:6.1f}% {s['p25']*100:+7.2f}% {s['p75']*100:+7.2f}% "
          f"{s['avg_win']*100:+7.2f}% {s['avg_loss']*100:+7.2f}% "
          f"{fe['med']*100:+7.2f}% {ae['med']*100:+7.2f}%")

print("\n=== same sweep: vr>=2 & 5-50亿 (for contrast) ===")
wide = [t for t in trades_raw
        if t["code"] in mcap and t["vr"] >= 2 and 5 <= mcap[t["code"]] / 1e8 < 50]
print(f"wide n={len(wide)}")
print(f"{'h':>3} {'n':>4} {'mean':>8} {'med':>8} {'win':>7}")
for h in (1, 2, 3, 4, 5, 6, 8, 10, 15):
    r = []
    for t in wide:
        j = t["i"] + 1 + (h - 1)
        if j < len(t["bars"]):
            r.append(ret(t["entry"], t["bars"][j]["close"]))
    s = sm(r)
    if s:
        print(f"{h:3} {s['n']:4} {s['mean']*100:+7.2f}% {s['med']*100:+7.2f}% {s['win']*100:6.1f}%")

print("\n=== target/stop on daily bars (order unknown; TP if MFE hit first assumed) ===")
print("path-dependent: if both MFE>=tp and MAE<=sl in same window, counted as BOTH-hit, not P&L")
for h in (3, 5, 8, 10):
    for tp, sl in ((0.06, -0.06), (0.08, -0.08), (0.10, -0.08),
                   (0.12, -0.08), (0.15, -0.10), (0.20, -0.10)):
        n = tp_n = sl_n = both = none = 0
        pnl = []
        for t in core:
            j = t["i"] + 1 + (h - 1)
            if j >= len(t["bars"]):
                continue
            window = t["bars"][t["i"] + 1:j + 1]
            mfe = ret(t["entry"], max(b["high"] for b in window))
            mae = ret(t["entry"], min(b["low"] for b in window))
            end = ret(t["entry"], t["bars"][j]["close"])
            n += 1
            hit_tp = mfe is not None and mfe >= tp
            hit_sl = mae is not None and mae <= sl
            if hit_tp and hit_sl:
                both += 1
            elif hit_tp:
                tp_n += 1
                pnl.append(tp)
            elif hit_sl:
                sl_n += 1
                pnl.append(sl)
            else:
                none += 1
                pnl.append(end)
        s = sm(pnl)
        print(f"h={h:2} tp={tp:+.0%} sl={sl:+.0%}  n={n:2}  "
              f"tp={tp_n:2} sl={sl_n:2} both={both:2} time={none:2}  "
              f"mean={s['mean']*100:+6.2f}% med={s['med']*100:+6.2f}% win={s['win']*100:5.1f}%")

print("\n=== calendar: days between signals, overlapping 5d holds ===")
days = sorted({t["date"] for t in core})
print("signal days", days)
from datetime import datetime
dts = [datetime.strptime(d, "%Y-%m-%d") for d in days]
gaps = [(dts[i] - dts[i - 1]).days for i in range(1, len(dts))]
if gaps:
    print(f"gap days min/med/max {min(gaps)} {sorted(gaps)[len(gaps)//2]} {max(gaps)}")
# overlap if next signal within 5 trading days of previous
print("signals", len(core), "in", len(days), "days over ~4 months; avg per week",
      round(len(core) / (16), 2), "(16 weeks)")
