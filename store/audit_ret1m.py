"""Audit min_ret_1m>=8% 13-trade slice: lookahead, current-mcap leak, overlap."""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.preprocess import calc_kline_features  # noqa: E402

KL = json.loads((ROOT / "store/backtest_klines.json").read_text())
HOLD, COOLDOWN, MAX_PER_DAY = 10, 14, 3


def ret(a, b):
    if not a or not b or a <= 0:
        return None
    return b / a - 1


def load_mcap(codes):
    from moomoo import OpenQuoteContext, RET_OK
    ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
    mcap_now, px_now = {}, {}
    try:
        for i in range(0, len(codes), 200):
            rc, data = ctx.get_market_snapshot(codes[i:i + 200])
            if rc != RET_OK:
                print("snap fail", data)
                continue
            for _, r in data.iterrows():
                mv = r.get("total_market_val")
                if mv is not None and mv == mv and mv > 0:
                    mcap_now[r["code"]] = float(mv) / 1e8
                lp = r.get("last_price")
                if lp is not None and lp == lp and lp > 0:
                    px_now[r["code"]] = float(lp)
    finally:
        ctx.close()
    return mcap_now, px_now


def pick(pool, extra=lambda t: True):
    last, per, out = {}, defaultdict(int), []
    for t in pool:
        if not extra(t):
            continue
        prev = last.get(t["code"])
        if prev:
            gap = (datetime.strptime(t["date"], "%Y-%m-%d")
                   - datetime.strptime(prev, "%Y-%m-%d")).days
            if gap < COOLDOWN:
                continue
        if per[t["date"]] >= MAX_PER_DAY:
            continue
        out.append(t)
        last[t["code"]] = t["date"]
        per[t["date"]] += 1
    return out


def stats(xs, label):
    rs = [t["r10"] for t in xs]
    if not rs:
        print(label, "empty")
        return
    w = sum(1 for x in rs if x > 0) / len(rs)
    print(
        f"{label}: n={len(xs)} unique={len({t['code'] for t in xs})} "
        f"mean={sum(rs)/len(rs)*100:+.1f}% win={w*100:.1f}%"
    )


def main():
    con = sqlite3.connect(ROOT / "store/signals.db")
    sig = con.execute(
        "SELECT code,name,scan_date,score,vol_ratio,change_rate "
        "FROM signals ORDER BY scan_date, score DESC"
    ).fetchall()
    con.close()
    codes = sorted({r[0] for r in sig if r[0] in KL})
    mcap_now, px_now = load_mcap(codes)

    rows = []
    for code, name, d, score, vr, chg in sig:
        bars = KL.get(code)
        if not bars:
            continue
        im = {b["date"]: i for i, b in enumerate(bars)}
        if d not in im or im[d] + 1 >= len(bars):
            continue
        i = im[d]
        feat = calc_kline_features(bars[: i + 1])
        if feat["ret_1m"] is None or i < 21:
            continue
        xj = i + 1 + HOLD - 1
        if xj >= len(bars):
            continue
        entry = bars[i + 1]["open"]
        if entry <= 0:
            continue
        scan_close = bars[i]["close"]
        r1_manual = ret(bars[i - 21]["close"], bars[i - 1]["close"])
        leak = ret(bars[i - 21]["close"], bars[i]["close"])
        mv_now = mcap_now.get(code)
        px = px_now.get(code) or bars[-1]["close"]
        mcap_then = (mv_now * scan_close / px) if mv_now and px else None
        rows.append({
            "code": code, "name": name, "date": d, "vr": vr, "chg": chg, "i": i,
            "entry_date": bars[i + 1]["date"], "exit_date": bars[xj]["date"],
            "scan_close": scan_close, "entry": entry, "exit": bars[xj]["close"],
            "gap": ret(scan_close, entry), "r10": ret(entry, bars[xj]["close"]),
            "ret_1m": feat["ret_1m"],
            "r1_manual": None if r1_manual is None else round(r1_manual, 4),
            "leak": None if leak is None else round(leak, 4),
            "mcap_now": mv_now, "mcap_then": mcap_then,
            "in_core_now": bool(mv_now and 2 <= vr <= 4 and 20 <= mv_now < 50),
            "in_core_then": bool(mcap_then and 2 <= vr <= 4 and 20 <= mcap_then < 50),
        })

    mismatch = [
        t for t in rows
        if t["r1_manual"] is not None
        and abs(t["ret_1m"] - t["r1_manual"]) > 1e-3
    ]
    print("ret_1m vs T-21→T-1 mismatches", len(mismatch))

    core_now = [t for t in rows if t["in_core_now"]]
    core_then = [t for t in rows if t["in_core_then"]]
    sel = pick(core_now, lambda t: t["ret_1m"] >= 0.08)
    sel_then = pick(core_then, lambda t: t["ret_1m"] >= 0.08)
    seen, uniq = set(), []
    for t in sel:
        if t["code"] in seen:
            continue
        seen.add(t["code"])
        uniq.append(t)

    print("\n=== trades (mcap=TODAY 20-50 + vr2-4 + r1m>=8%) ===")
    hdr = (f"{'scan':<12}{'code':<10}{'entry':<12}{'exit':<12}"
           f"{'px_in':>8}{'px_out':>8}{'gap':>7}{'r1m':>8}{'r10':>8}"
           f"{'now亿':>7}{'then亿':>7}{'thenOK':>7}")
    print(hdr)
    for t in sel:
        print(
            f"{t['date']:<12}{t['code']:<10}{t['entry_date']:<12}{t['exit_date']:<12}"
            f"{t['entry']:8.3f}{t['exit']:8.3f}{(t['gap'] or 0)*100:6.1f}%"
            f"{t['ret_1m']*100:7.1f}%{t['r10']*100:7.1f}%"
            f"{t['mcap_now']:7.1f}{(t['mcap_then'] or 0):7.1f}"
            f"{'Y' if t['in_core_then'] else 'N':>7}"
        )

    print()
    stats(sel, "reported slice (mcap TODAY)")
    stats(sel_then, "mcap at SCAN (price-scaled from today)")
    stats(uniq, "first name only (drop 01489/01793 repeats)")
    stats(pick(core_now, lambda t: True), "no ret_1m filter, mcap TODAY")

    print("\n=== same-name / overlapping holds ===")
    by = defaultdict(list)
    for t in sel:
        by[t["code"]].append(t)
    for code, ts in by.items():
        if len(ts) > 1:
            print(code, [(x["date"], x["entry_date"], x["exit_date"],
                          f"{x['r10']*100:+.1f}%") for x in ts])

    print("\n=== 01710 around 2026-09-02 ===")
    bars, i = KL["HK.01710"], {b["date"]: n for n, b in enumerate(KL["HK.01710"])}["2026-09-02"]
    for b in KL["HK.01710"][i:i + 12]:
        print(b["date"], f"O={b['open']:.3f} H={b['high']:.3f} "
              f"L={b['low']:.3f} C={b['close']:.3f} V={int(b['volume'])}")

    print("\n=== winners that fail 20-50亿 at scan (scaled) ===")
    for t in sel:
        if not t["in_core_then"]:
            print(f"  {t['date']} {t['code']} then={t['mcap_then']:.1f} "
                  f"now={t['mcap_now']:.1f} r10={t['r10']*100:+.1f}%")

    print("\n=== scan-day +8% is NOT inside ret_1m ===")
    extras = [(t["leak"] - t["ret_1m"]) * 100 for t in sel if t["leak"] is not None]
    print(f"  if 1m included scan close, extra {min(extras):+.1f}% to {max(extras):+.1f}% "
          f"(equal to that day's move)")


if __name__ == "__main__":
    main()
