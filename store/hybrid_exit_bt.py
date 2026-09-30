"""Research-only: 5-50 scan-date mcap + vr 2-4 + ret_1m>=8%, hybrid exits.

Does not change live FILTER/EXIT_CONFIG.
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

from engine.exits import simulate_exit, sma
from engine.preprocess import calc_kline_features

KL = json.loads((ROOT / "store/backtest_klines.json").read_text())
COOLDOWN_DAYS, MAX_PER_DAY = 14, 3
VR_LO, VR_HI = 2.0, 4.0
SKIP = {"HK.02958", "HK.08603"}


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
    return {
        "n": n, "mean": sum(xs) / n, "med": xs[n // 2],
        "win": len(wins) / n, "best": xs[-1], "worst": xs[0],
        "p25": xs[int((n - 1) * 0.25)],
    }


def fmt(s, h=None):
    if not s:
        return "n=  0"
    extra = f"  hold={h:.1f}d" if h is not None else ""
    return (
        f"n={s['n']:3}  mean={s['mean']*100:+6.1f}%  med={s['med']*100:+6.1f}%  "
        f"p25={s['p25']*100:+5.1f}%  win={s['win']*100:4.0f}%  "
        f"worst={s['worst']*100:+5.1f}%{extra}"
    )


def load_mcap(codes):
    from moomoo import RET_OK, OpenQuoteContext
    ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
    meta, last_px = {}, {}
    try:
        for i in range(0, len(codes), 80):
            batch = codes[i:i + 80]
            rc, data = ctx.get_market_snapshot(batch)
            rows = []
            if rc != RET_OK:
                for c in batch:
                    rc2, data2 = ctx.get_market_snapshot([c])
                    if rc2 == RET_OK:
                        rows.append(data2)
            else:
                rows.append(data)
            for df in rows:
                for _, r in df.iterrows():
                    mv, lp = r.get("total_market_val"), r.get("last_price")
                    if mv == mv and mv and mv > 0:
                        meta[r["code"]] = float(mv)
                    if lp == lp and lp and lp > 0:
                        last_px[r["code"]] = float(lp)
    finally:
        ctx.close()
    return meta, last_px


def pick(pool):
    last_seen, per_day, out = {}, defaultdict(int), []
    for t in pool:
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


def time_cfg(hold=10, hard=None):
    return {"hold_max_days": hold, "hard_stop_pct": hard, "ratchet_tiers": []}


def sim_custom(t, kind, hard=None, hold=20, bb_only_red=False,
               green_time=None, run_if_mfe=None):
    bars, entry_idx, entry = t["bars"], t["i"] + 1, t["entry"]
    closes = [float(b["close"]) for b in bars]
    peak = entry
    last = min(entry_idx + hold, len(bars))
    armed = run_if_mfe is None
    for j in range(entry_idx, last):
        b = bars[j]
        day = j - entry_idx + 1
        if hard:
            hard_px = entry * (1 - hard)
            if b["low"] <= hard_px:
                return {"exit_px": hard_px, "reason": "HARD",
                        "hold_days": day, "peak": peak}
        peak = max(peak, b["high"])
        if run_if_mfe is not None and (peak / entry - 1) >= run_if_mfe:
            armed = True
        mid = sma(closes, j - 1, 20)
        hit_bb = kind in {"bb", "hybrid"} and mid and float(b["close"]) <= mid
        if hit_bb and ((not bb_only_red) or float(b["close"]) < entry):
            return {"exit_px": float(b["close"]), "reason": "BB",
                    "hold_days": day, "peak": peak}
        if green_time and day >= green_time and float(b["close"]) >= entry:
            return {"exit_px": float(b["close"]), "reason": "GREEN_T",
                    "hold_days": day, "peak": peak}
        if kind == "time" and day >= hold:
            return {"exit_px": b["close"], "reason": "TIME",
                    "hold_days": day, "peak": peak}
        if kind == "bb" and day >= hold:
            return {"exit_px": b["close"], "reason": "TIME",
                    "hold_days": day, "peak": peak}
        if kind == "hybrid":
            if not armed and day >= 10:
                return {"exit_px": b["close"], "reason": "TIME10",
                        "hold_days": day, "peak": peak}
            if armed and day >= hold:
                return {"exit_px": b["close"], "reason": "TIME",
                        "hold_days": day, "peak": peak}
    b = bars[last - 1]
    return {"exit_px": b["close"], "reason": "END",
            "hold_days": last - entry_idx, "peak": peak}


def eval_fn(subset, fn):
    rets, holds, reasons = [], [], defaultdict(int)
    for t in subset:
        x = fn(t)
        if not x:
            continue
        r = ret(t["entry"], x["exit_px"])
        if r is None:
            continue
        rets.append(r)
        holds.append(x["hold_days"])
        reasons[x["reason"]] += 1
    s = sm(rets)
    h = sum(holds) / len(holds) if holds else 0
    return s, h, dict(reasons)


def main():
    conn = sqlite3.connect(ROOT / "store/signals.db")
    sig = conn.execute(
        "SELECT code, name, scan_date, score, vol_ratio FROM signals "
        "ORDER BY scan_date, score DESC"
    ).fetchall()
    conn.close()
    codes = sorted({c for c, *_ in sig if c not in SKIP})
    meta, last_px = load_mcap(codes)
    print(f"snapshot mcap {len(meta)}/{len(codes)}")

    cands = []
    for code, name, d, score, vr in sig:
        bars = KL.get(code)
        if not bars:
            continue
        im = {b["date"]: i for i, b in enumerate(bars)}
        if d not in im or im[d] + 1 >= len(bars):
            continue
        i = im[d]
        feat = calc_kline_features(bars[: i + 1])
        if feat["ret_1m"] is None:
            continue
        entry = bars[i + 1]["open"]
        if entry <= 0:
            continue
        today_mv = meta.get(code)
        px_now = last_px.get(code) or bars[-1]["close"]
        if not (today_mv and px_now and bars[i]["close"]):
            continue
        scan_mcap = (today_mv * bars[i]["close"] / px_now) / 1e8
        closes = [float(b["close"]) for b in bars]
        mid_entry = sma(closes, i, 20)
        ext = (entry / mid_entry - 1) if mid_entry else None
        end10 = min(i + 1 + 10, len(bars))
        window = bars[i + 1:end10]
        mae = ret(entry, min(b["low"] for b in window)) if window else None
        mfe = ret(entry, max(b["high"] for b in window)) if window else None
        cands.append({
            "code": code, "name": name or code, "date": d, "score": score,
            "vr": vr, "ret_1m": feat["ret_1m"], "i": i, "bars": bars,
            "entry": entry, "scan_mcap": scan_mcap, "ext": ext,
            "mae10": mae, "mfe10": mfe,
        })

    core = [
        t for t in cands
        if t["vr"] is not None and VR_LO <= t["vr"] <= VR_HI
        and 5 <= t["scan_mcap"] < 50 and t["ret_1m"] >= 0.08
    ]
    picked = pick(core)
    p20 = [t for t in picked if t["scan_mcap"] >= 20]
    p_lo = [t for t in picked if t["scan_mcap"] < 20]
    print(f"5-50 + vr2-4 + r1m>=8%  pool={len(core)} picked={len(picked)}  "
          f"5-20={len(p_lo)} 20-50={len(p20)}")

    print("\n--- 10日最差 8 笔 ---")
    ranked = []
    for t in picked:
        x = simulate_exit(t["bars"], t["i"] + 1, t["entry"], time_cfg(10))
        r = ret(t["entry"], x["exit_px"]) if x else None
        ranked.append((r, t))
    ranked.sort(key=lambda z: 9 if z[0] is None else z[0])
    for r, t in ranked[:8]:
        ext = "n/a" if t["ext"] is None else f"{t['ext']*100:+.0f}%"
        mae = "n/a" if t["mae10"] is None else f"{t['mae10']*100:+.0f}%"
        mfe = "n/a" if t["mfe10"] is None else f"{t['mfe10']*100:+.0f}%"
        print(f"  {t['date']} {t['code']} {t['name'][:12]:12} "
              f"mcap={t['scan_mcap']:5.1f} r10={r*100:+6.1f}% "
              f"mae={mae} mfe={mfe} ext={ext} r1m={t['ret_1m']*100:+.0f}%")

    variants = []

    def add(name, subset, fn):
        s, h, rs = eval_fn(subset, fn)
        variants.append((name, s, h, rs))

    add("1  20-50  10日", p20,
        lambda t: simulate_exit(t["bars"], t["i"] + 1, t["entry"], time_cfg(10)))
    add("2  5-50   10日", picked,
        lambda t: simulate_exit(t["bars"], t["i"] + 1, t["entry"], time_cfg(10)))
    add("3  5-50   BB20", picked, lambda t: sim_custom(t, "bb", hold=20))
    add("4  5-50   10日+硬-15%", picked,
        lambda t: simulate_exit(t["bars"], t["i"] + 1, t["entry"], time_cfg(10, 0.15)))
    add("5  5-50   10日+硬-20%", picked,
        lambda t: simulate_exit(t["bars"], t["i"] + 1, t["entry"], time_cfg(10, 0.20)))
    add("6  5-50   10日+硬-25%", picked,
        lambda t: simulate_exit(t["bars"], t["i"] + 1, t["entry"], time_cfg(10, 0.25)))
    add("7  5-50   BB20+硬-15%", picked,
        lambda t: sim_custom(t, "bb", hard=0.15, hold=20))
    add("8  5-50   BB20+硬-20%", picked,
        lambda t: sim_custom(t, "bb", hard=0.20, hold=20))
    add("9  5-50   BB20+硬-25%", picked,
        lambda t: sim_custom(t, "bb", hard=0.25, hold=20))
    add("10 5-50   布林只砍亏 最多20日", picked,
        lambda t: sim_custom(t, "bb", hold=20, bb_only_red=True))
    add("11 5-50   布林只砍亏+硬-20%", picked,
        lambda t: sim_custom(t, "bb", hard=0.20, hold=20, bb_only_red=True))
    add("12 5-50   赚了第10日卖，亏了跟布林最多20", picked,
        lambda t: sim_custom(t, "bb", hold=20, green_time=10, bb_only_red=True))
    add("13 5-50   峰值≥20%才跟布林否则10日", picked,
        lambda t: sim_custom(t, "hybrid", hold=20, run_if_mfe=0.20))
    add("14 5-50   峰值≥20%跟布林+硬-20%", picked,
        lambda t: sim_custom(t, "hybrid", hard=0.20, hold=20, run_if_mfe=0.20))

    def skip_hot(t, fn, thr=0.25):
        if t["ext"] is not None and t["ext"] > thr:
            return None
        return fn(t)

    add("15 5-50   10日 排除入场相对SMA>+25%", picked,
        lambda t: skip_hot(t, lambda u: simulate_exit(
            u["bars"], u["i"] + 1, u["entry"], time_cfg(10))))
    add("16 5-50   BB20 排除过热>+25%", picked,
        lambda t: skip_hot(t, lambda u: sim_custom(u, "bb", hold=20)))
    add("17 5-50   BB+硬-20% 排除过热>+25%", picked,
        lambda t: skip_hot(t, lambda u: sim_custom(u, "bb", hard=0.20, hold=20)))
    add("18 5-50   10日+硬-20% 排除过热>+25%", picked,
        lambda t: skip_hot(t, lambda u: simulate_exit(
            u["bars"], u["i"] + 1, u["entry"], time_cfg(10, 0.20))))
    add("19 5-20用BB+硬-20%，20-50用10日", picked,
        lambda t: (sim_custom(t, "bb", hard=0.20, hold=20)
                   if t["scan_mcap"] < 20
                   else simulate_exit(t["bars"], t["i"] + 1, t["entry"], time_cfg(10))))
    add("20 5-20用10日+硬-20%，20-50用10日", picked,
        lambda t: simulate_exit(
            t["bars"], t["i"] + 1, t["entry"],
            time_cfg(10, 0.20 if t["scan_mcap"] < 20 else None)))

    print("\n=== 对照 ===")
    for name, s, h, rs in variants:
        print(f"{name:<44} {fmt(s, h)}  {rs}")

    print("\n--- 入场相对昨收SMA20 分桶（10日）---")
    bins = [(-9, 0, "低于中轨"), (0, 0.10, "0-10%"),
            (0.10, 0.25, "10-25%"), (0.25, 9, ">25%")]
    for lo, hi, lab in bins:
        xs = []
        for t in picked:
            if t["ext"] is None or not (lo <= t["ext"] < hi):
                continue
            x = simulate_exit(t["bars"], t["i"] + 1, t["entry"], time_cfg(10))
            xs.append(ret(t["entry"], x["exit_px"]))
        print(f"  {lab:<10} {fmt(sm(xs))}")

    print("\n--- 入场相对 SMA20 上限（5-50 池内再筛，10日 vs BB）---")
    for thr in (0.25, 0.30, 0.35, 0.40, 0.50, 0.60):
        sub = [t for t in picked if t["ext"] is not None and t["ext"] <= thr]
        s10, h10, _ = eval_fn(
            sub,
            lambda t: simulate_exit(
                t["bars"], t["i"] + 1, t["entry"], time_cfg(10)))
        sbb, hbb, _ = eval_fn(sub, lambda t: sim_custom(t, "bb", hold=20))
        print(f"  ext≤{thr*100:.0f}%  n={len(sub):2}  "
              f"10日 {fmt(s10)} | BB {fmt(sbb, hbb)}")

    print("\n--- 5-20 vs 20-50 拆开 ---")
    for lab, sub in [("5-20", p_lo), ("20-50", p20)]:
        print(f" {lab} n={len(sub)}")
        for nm, fn in [
            ("10日", lambda t: simulate_exit(
                t["bars"], t["i"] + 1, t["entry"], time_cfg(10))),
            ("BB20", lambda t: sim_custom(t, "bb", hold=20)),
            ("BB+硬-20", lambda t: sim_custom(t, "bb", hard=0.20, hold=20)),
            ("10日+硬-20", lambda t: simulate_exit(
                t["bars"], t["i"] + 1, t["entry"], time_cfg(10, 0.20))),
        ]:
            s, h, rs = eval_fn(sub, fn)
            print(f"   {nm:<12} {fmt(s, h)}  {rs}")


if __name__ == "__main__":
    main()
