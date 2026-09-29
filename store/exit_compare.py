"""Compare time-exit vs TF ratchet / TF fixed trail on the core slice."""
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moomoo import OpenQuoteContext, RET_OK

from config import EXIT_CONFIG
from engine.exits import simulate_exit

KL = json.loads(Path(__file__).with_name("backtest_klines.json").read_text())
conn = sqlite3.connect(Path(__file__).with_name("signals.db"))
sig = conn.execute(
    "SELECT code, name, scan_date, vol_ratio FROM signals"
).fetchall()
conn.close()


def sm(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    xs = sorted(xs)
    n = len(xs)
    return n, sum(xs) / n, xs[n // 2], sum(1 for x in xs if x > 0) / n


raw = []
for code, name, d, vr in sig:
    bars = KL.get(code)
    if not bars:
        continue
    im = {b["date"]: i for i, b in enumerate(bars)}
    if d not in im or im[d] + 1 >= len(bars):
        continue
    i = im[d] + 1
    entry = bars[i]["open"]
    if entry <= 0:
        continue
    raw.append(dict(code=code, name=name, date=d, vr=vr, bars=bars, i=i, entry=entry))

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

core = [t for t in raw if t["vr"] >= 2 and t["code"] in mcap
        and 20 <= mcap[t["code"]] / 1e8 < 50]
print("core", len(core))

cfgs = {
    "time_10d": {**EXIT_CONFIG, "hard_stop_pct": 1.0, "ratchet_tiers": [(0, 1.0)]},
    "tf_live_8pct": {**EXIT_CONFIG, "ratchet_tiers": [(0.0, 0.08)]},
    "tf_ratchet": EXIT_CONFIG,
    "extreme_18_10_6": {**EXIT_CONFIG, "ratchet_tiers": [(0.0, 0.18), (0.30, 0.10), (0.60, 0.06)]},
}

for name, cfg in cfgs.items():
    rets, reasons, holds = [], Counter(), []
    for t in core:
        x = simulate_exit(t["bars"], t["i"], t["entry"], cfg)
        if not x:
            continue
        rets.append(x["exit_px"] / t["entry"] - 1)
        reasons[x["reason"]] += 1
        holds.append(x["hold_days"])
    s = sm(rets)
    print(f"\n{name}: n={s[0]} mean={s[1]*100:+.1f}% med={s[2]*100:+.1f}% "
          f"win={s[3]*100:.1f}%  hold_avg={sum(holds)/len(holds):.1f}  {dict(reasons)}")
