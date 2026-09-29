"""槽位 × 单槽金额网格，口径对齐 YiDong 那张表。

选股：量比>=2、市值 20-50 亿、14 日历日冷却、每天最多 3 只。
交易：次日开盘买，第 10 个交易日收盘卖。整手买不起则跳过（信号有、成交无）。
盈亏曲线按每日收盘盯市，回撤用盯市净值。
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from moomoo import OpenQuoteContext, RET_OK

ROOT = Path(__file__).resolve().parents[1]
KL = json.loads((ROOT / "store/backtest_klines.json").read_text())
HOLD = 10
COOLDOWN_DAYS = 14
MAX_PER_DAY = 3

CONFIGS = [
    (1, 25000),
    (1, 30000),
    (2, 15000),
    (2, 20000),
    (2, 25000),
    (3, 10000),
    (3, 15000),
    (3, 20000),
]


def sm_pnl(xs):
    return sum(xs), min(xs) if xs else 0


conn = sqlite3.connect(ROOT / "store/signals.db")
sig = conn.execute(
    "SELECT code, name, scan_date, score, vol_ratio FROM signals ORDER BY scan_date, score DESC"
).fetchall()
conn.close()

cands = []
for code, name, d, score, vr in sig:
    bars = KL.get(code)
    if not bars:
        continue
    im = {b["date"]: i for i, b in enumerate(bars)}
    if d not in im or im[d] + 1 >= len(bars):
        continue
    ei = im[d] + 1
    xj = ei + HOLD - 1
    if xj >= len(bars):
        continue
    entry = bars[ei]["open"]
    if entry <= 0:
        continue
    cands.append({
        "code": code, "name": name or code, "date": d, "score": score,
        "vr": vr, "bars": bars, "ei": ei, "xj": xj,
        "entry": entry, "exit": bars[xj]["close"],
        "entry_date": bars[ei]["date"], "exit_date": bars[xj]["date"],
        "ret": bars[xj]["close"] / entry - 1,
    })

codes = sorted({c["code"] for c in cands})
ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
meta = {}
try:
    for i in range(0, len(codes), 200):
        rc, data = ctx.get_market_snapshot(codes[i:i + 200])
        if rc != RET_OK:
            continue
        for _, r in data.iterrows():
            mv = r.get("total_market_val")
            lot = int(r.get("lot_size", 100) or 100)
            if mv is not None and mv == mv and mv > 0:
                meta[r["code"]] = {"mcap_b": float(mv) / 1e8, "lot": lot}
finally:
    ctx.close()

core = []
last_seen = {}
per_day = defaultdict(int)
for c in cands:
    m = meta.get(c["code"])
    if not m or c["vr"] < 2 or not (20 <= m["mcap_b"] < 50):
        continue
    prev = last_seen.get(c["code"])
    if prev:
        gap = (datetime.strptime(c["date"], "%Y-%m-%d")
               - datetime.strptime(prev, "%Y-%m-%d")).days
        if gap < COOLDOWN_DAYS:
            continue
    if per_day[c["date"]] >= MAX_PER_DAY:
        continue
    c = dict(c)
    c["lot"] = m["lot"]
    c["lot_cost"] = c["lot"] * c["entry"]
    core.append(c)
    last_seen[c["code"]] = c["date"]
    per_day[c["date"]] += 1

print(f"signals after filter/cooldown {len(core)}  "
      f"unique {len({c['code'] for c in core})}  "
      f"days {len({c['date'] for c in core})}")
print(f"lot_cost p50={sorted(c['lot_cost'] for c in core)[len(core)//2]:.0f}  "
      f"max={max(c['lot_cost'] for c in core):.0f}")


def close_on(bars, date):
    for b in bars:
        if b["date"] == date:
            return b["close"]
    return None


def run(slots: int, slot_hkd: int):
    occupied = []  # list of (exit_date, trade)
    trades = []
    skipped_lot = skipped_slot = 0
    for c in core:
        occupied = [o for o in occupied if o[0] > c["entry_date"]]
        if len(occupied) >= slots:
            skipped_slot += 1
            continue
        n_lots = int(slot_hkd // c["lot_cost"])
        if n_lots <= 0:
            skipped_lot += 1
            continue
        qty = n_lots * c["lot"]
        cost = qty * c["entry"]
        pnl = qty * (c["exit"] - c["entry"])
        t = {**c, "qty": qty, "cost": cost, "pnl": pnl}
        trades.append(t)
        occupied.append((c["exit_date"], t))

    # daily MTM equity
    if not trades:
        return None
    days = sorted({b["date"] for t in trades for b in t["bars"]
                   if t["entry_date"] <= b["date"] <= t["exit_date"]})
    equity = []
    realized = 0.0
    closed = set()
    peak = dd = 0.0
    for d in days:
        ur = 0.0
        for i, t in enumerate(trades):
            if t["entry_date"] <= d < t["exit_date"]:
                px = close_on(t["bars"], d)
                if px:
                    ur += t["qty"] * (px - t["entry"])
            elif d == t["exit_date"] and i not in closed:
                realized += t["pnl"]
                closed.add(i)
        eq = realized + ur
        equity.append(eq)
        peak = max(peak, eq)
        dd = min(dd, eq - peak)

    pnls = [t["pnl"] for t in trades]
    total = sum(pnls)
    max_dd = dd
    worst = min(pnls)
    ratio = abs(total / max_dd) if max_dd < 0 else None
    return {
        "slots": slots, "slot": slot_hkd, "notional": slots * slot_hkd,
        "signals": len(core), "fills": len(trades),
        "skip_lot": skipped_lot, "skip_slot": skipped_slot,
        "pnl": total, "dd": max_dd, "worst": worst, "ratio": ratio,
        "avg_cost": sum(t["cost"] for t in trades) / len(trades),
        "win": sum(1 for p in pnls if p > 0) / len(pnls),
    }


print(f"\n{'配置':<16}{'总敞口':>8}{'信号':>6}{'成交':>6}{'跳过整手':>8}{'跳过槽位':>8}"
      f"{'累计盈亏':>10}{'最大回撤':>10}{'单笔最大亏':>10}{'收益/回撤':>8}{'胜率':>7}")
rows = []
for sl, hkd in CONFIGS:
    r = run(sl, hkd)
    rows.append(r)
    ratio = f"{r['ratio']:.2f}" if r["ratio"] else "—"
    print(f"{sl}槽 × {hkd:<7} {r['notional']:8}{r['signals']:6}{r['fills']:6}"
          f"{r['skip_lot']:8}{r['skip_slot']:8}"
          f"{r['pnl']:+10.0f}{r['dd']:10.0f}{r['worst']:10.0f}{ratio:>8}{r['win']*100:6.1f}%")

(ROOT / "store/slot_stats.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))
print("\nwrote store/slot_stats.json")
