"""对照：现行 20-50亿+量比2-4，再叠近1月反转 / 缩量硬过滤。

入场 T+1 开盘，持 10 个交易日收盘；14 日历日冷却、每天最多 3 只。
近 1 月 = 信号日前 21 个交易日、不含当日大阳（short-term reversal）。
12-1 = 再往前约 231 个交易日（中间动量）。缩量用打分同款，默认看剔除当日的 contraction_pre。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.preprocess import calc_kline_features

KL = json.loads((ROOT / "store/backtest_klines.json").read_text())
HOLD = 10
COOLDOWN_DAYS = 14
MAX_PER_DAY = 3
VR_LO, VR_HI = 2.0, 4.0
MCAP_LO, MCAP_HI = 20.0, 50.0


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
        "win": len(wins) / n,
        "avg_win": sum(wins) / len(wins) if wins else 0,
        "avg_loss": sum(loss) / len(loss) if loss else 0,
        "best": xs[-1],
        "worst": xs[0],
    }


def fmt(s):
    if not s:
        return "n=  0"
    return (f"n={s['n']:3}  mean={s['mean']*100:+6.2f}%  med={s['med']*100:+6.2f}%  "
            f"win={s['win']*100:5.1f}%  avgW={s['avg_win']*100:+6.1f}%  "
            f"avgL={s['avg_loss']*100:+6.1f}%  worst={s['worst']*100:+6.1f}%")


def load_mcap(codes: list[str]) -> dict[str, float]:
    try:
        from moomoo import OpenQuoteContext, RET_OK
    except ImportError:
        return {}
    ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
    meta = {}
    try:
        for i in range(0, len(codes), 200):
            rc, data = ctx.get_market_snapshot(codes[i:i + 200])
            if rc != RET_OK:
                print("snap fail", data)
                continue
            for _, r in data.iterrows():
                mv = r.get("total_market_val")
                if mv is not None and mv == mv and mv > 0:
                    meta[r["code"]] = float(mv) / 1e8
    finally:
        ctx.close()
    return meta


conn = sqlite3.connect(ROOT / "store/signals.db")
sig = conn.execute(
    "SELECT code, name, scan_date, score, vol_ratio FROM signals ORDER BY scan_date, score DESC"
).fetchall()
conn.close()

raw = []
skip_feat = 0
for code, name, d, score, vr in sig:
    bars = KL.get(code)
    if not bars:
        continue
    im = {b["date"]: i for i, b in enumerate(bars)}
    if d not in im or im[d] + 1 >= len(bars):
        continue
    i = im[d]
    hist = bars[: i + 1]
    feat = calc_kline_features(hist)
    if feat["ret_1m"] is None:
        skip_feat += 1
        continue
    xj = i + 1 + HOLD - 1
    if xj >= len(bars):
        continue
    entry = bars[i + 1]["open"]
    if entry <= 0:
        continue
    r10 = ret(entry, bars[xj]["close"])
    if r10 is None:
        continue
    raw.append({
        "code": code, "name": name or code, "date": d, "score": score, "vr": vr,
        "ret_1m": feat["ret_1m"],
        "ret_12_1": feat["ret_12_1"],
        "c_pre": feat["contraction_pre"],
        "c_all": feat["contraction_ratio"],
        "r10": r10,
        "entry": entry,
    })

codes = sorted({t["code"] for t in raw})
print(f"with 1m lookback {len(raw)}  skipped_no_1m {skip_feat}  "
      f"kline_first={min((KL[c][0]['date'] for c in codes), default='?')}")
mcap = load_mcap(codes)
print(f"mcap coverage {len(mcap)}/{len(codes)}")


def in_core(t):
    mv = mcap.get(t["code"])
    if mv is None:
        return False
    return VR_LO <= t["vr"] <= VR_HI and MCAP_LO <= mv < MCAP_HI


def pick(pool, extra=lambda t: True):
    last_seen = {}
    per_day = defaultdict(int)
    out = []
    for t in pool:
        if not extra(t):
            continue
        prev = last_seen.get(t["code"])
        if prev:
            gap = (datetime.strptime(t["date"], "%Y-%m-%d")
                   - datetime.strptime(prev, "%Y-%m-%d")).days
            if gap < COOLDOWN_DAYS:
                continue
        if per_day[t["date"]] >= MAX_PER_DAY:
            continue
        out.append(t)
        last_seen[t["code"]] = t["date"]
        per_day[t["date"]] += 1
    return out


core_pool = [t for t in raw if in_core(t)]
print(f"\ncore pool (vr {VR_LO}-{VR_HI}, mcap {MCAP_LO}-{MCAP_HI}亿) n={len(core_pool)}")

print("\n=== 现行切片上，近1月 / 缩量 分布（未再截断每天3只） ===")


def buckets(xs, key, edges, labels):
    groups = {lb: [] for lb in labels}
    for t in xs:
        v = t[key]
        if v is None:
            continue
        placed = False
        for i, e in enumerate(edges):
            if v < e:
                groups[labels[i]].append(t["r10"])
                placed = True
                break
        if not placed:
            groups[labels[-1]].append(t["r10"])
    for lb in labels:
        print(f"  {lb:<16} {fmt(sm(groups[lb]))}")


print("ret_1m (昨收相对 21 日前)")
buckets(
    core_pool, "ret_1m",
    [-0.15, -0.05, 0.0, 0.10],
    ["< -15%", "-15~-5%", "-5~0%", "0~10%", ">=10%"],
)
print("contraction_pre (近15日不含当日)")
buckets(
    core_pool, "c_pre",
    [0.3, 0.4, 0.5, 0.7],
    ["<0.3", "0.3-0.4", "0.4-0.5", "0.5-0.7", ">=0.7"],
)
print("ret_12_1 (有数据才分)")
has12 = [t for t in core_pool if t["ret_12_1"] is not None]
print(f"  have 12-1: {len(has12)}/{len(core_pool)}")
if has12:
    buckets(
        has12, "ret_12_1",
        [-0.2, 0.0, 0.2],
        ["< -20%", "-20~0%", "0~20%", ">=20%"],
    )

print("\n=== 冷却+每天最多3只 后，叠加规则对照（持10日） ===")
rules = [
    ("现行(量比2-4,20-50亿)", lambda t: True),
    ("+ 近1月<=0", lambda t: t["ret_1m"] <= 0),
    ("+ 近1月<=-5%", lambda t: t["ret_1m"] <= -0.05),
    ("+ 近1月<=+5%", lambda t: t["ret_1m"] <= 0.05),
    ("+ 缩量pre>=0.4", lambda t: t["c_pre"] >= 0.4),
    ("+ 缩量pre>=0.5", lambda t: t["c_pre"] >= 0.5),
    ("+ 缩量含当日>=0.4", lambda t: t["c_all"] >= 0.4),
    ("+ 1月<=0 且缩量>=0.4", lambda t: t["ret_1m"] <= 0 and t["c_pre"] >= 0.4),
    ("+ 1月<=0 且缩量>=0.5", lambda t: t["ret_1m"] <= 0 and t["c_pre"] >= 0.5),
    ("+ 12-1>0 且1月<=0", lambda t: t["ret_12_1"] is not None and t["ret_12_1"] > 0 and t["ret_1m"] <= 0),
]

rows = []
for name, fn in rules:
    picked = pick(core_pool, fn)
    s = sm([t["r10"] for t in picked])
    print(f"{name:<22} {fmt(s)}")
    rows.append({"rule": name, "stats": s, "n": 0 if not s else s["n"]})

print("\n=== 对照：不加市值/量比，只靠 1月反转+缩量（仍冷却+每天3） ===")
alt = [
    ("全样本 每天3", lambda t: True),
    ("全样本 1月<=0", lambda t: t["ret_1m"] <= 0),
    ("全样本 缩量>=0.4", lambda t: t["c_pre"] >= 0.4),
    ("全样本 1月<=0+缩量>=0.4", lambda t: t["ret_1m"] <= 0 and t["c_pre"] >= 0.4),
]
for name, fn in alt:
    picked = pick(raw, fn)
    s = sm([t["r10"] for t in picked])
    print(f"{name:<24} {fmt(s)}")

out = ROOT / "store/reversal_filter_stats.json"
out.write_text(json.dumps({
    "core_n": len(core_pool),
    "rules": [
        {"rule": r["rule"], **({} if not r["stats"] else r["stats"])}
        for r in rows
    ],
}, ensure_ascii=False, indent=2, default=str))
print("\nwrote", out)
