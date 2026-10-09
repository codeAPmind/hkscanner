"""量能维度（30分）改版回测：老打分 vs 缩量后放量（V1 线上 / V2 / V3）。

两个候选池：
  signals  = store/signals.db 历史推送（与 hybrid_exit_bt 同口径；老分 = 库内分，换维度用差值）
  universe = 全港股 yfinance 日线重建快照（量比≈今量/前5日均量，市值≈现股本×当日收盘，
             换手≈量/流通股本，52周高低=近250根），逐日跑 initial_filter→pass_kline_filter→score_pattern
现行策略：5-50亿、量比2-4、涨幅>=8%、回撤>=35%、距高>=75%、ret_1m>=8%、ext_sma20<=40%、
min_score 60、每天前3、28 日历日冷却、T+1 开盘买（开盘相对昨收 SMA20 >40% 不买）、
出场 bb_mid（昨收 SMA20）最多 20 个交易日（engine.exits.simulate_exit + EXIT_CONFIG）。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import engine.pattern_scorer as ps
from config import EXIT_CONFIG, FILTER_CONFIG
from engine.exits import simulate_exit, sma
from engine.filter import pass_kline_filter
from engine.preprocess import calc_kline_features

NEW_SCORE_VOLUME = ps.score_volume


# ── 维度2 各版本（返回 (pts, signal, flags)，与 ps.score_volume 同签名）
def vol_legacy(vr, kfeat):
    if vr < 0.8:
        return 30, ("", "", ""), []
    if vr < 1.2:
        return 22, ("", "", ""), []
    if vr < 2.0:
        return 12, ("", "", ""), []
    if vr < 3.5:
        return 5, ("", "", ""), []
    return 0, ("", "", ""), []


def _c(kfeat):
    c = kfeat.get("contraction_pre")
    return float(c if c is not None else kfeat.get("contraction_ratio", 0) or 0)


def vol_v2(vr, kfeat):  # 15/15 等权
    c = _c(kfeat)
    cp = 15 if c >= 0.6 else 10 if c >= 0.4 else 5 if c >= 0.2 else 0
    vp = (15 if 2.0 <= vr <= 3.0 else 9 if 3.0 < vr <= 3.5 else 4 if 3.5 < vr <= 4.0
          else 7 if 1.5 <= vr < 2.0 else 0)
    return cp + vp, ("", "", ""), []


def vol_v3(vr, kfeat):  # 乘积：没缩量就没分
    c = _c(kfeat)
    f = min(c / 0.6, 1.0)
    g = (1.0 if 2.0 <= vr <= 3.0 else 0.67 if 3.0 < vr <= 3.5 else 0.33 if 3.5 < vr <= 4.0
         else 0.5 if 1.5 <= vr < 2.0 else 0.0)
    return int(round(30 * f * g)), ("", "", ""), []


VARIANTS = {"base": vol_legacy, "V1": NEW_SCORE_VOLUME, "V2": vol_v2, "V3": vol_v3}
F = FILTER_CONFIG
COOLDOWN = F["signal_cooldown_days"]
TOPN = F["max_results"]
MAX_EXT = F["max_ext_sma20"]


def ret(a, b):
    return None if not a or a <= 0 or b is None else b / a - 1


# ───────────────────────── 候选池
def pool_signals():
    KL = json.loads((ROOT / "store/backtest_klines.json").read_text())
    from store.hybrid_exit_bt import load_mcap
    rows = sqlite3.connect(ROOT / "store/signals.db").execute(
        "SELECT code, name, scan_date, score, vol_ratio, change_rate FROM signals").fetchall()
    meta, last_px = load_mcap(sorted({r[0] for r in rows}))
    out = []
    for code, name, d, score, vr, chg in rows:
        bars = KL.get(code)
        if not bars or vr is None:
            continue
        im = {b["date"]: i for i, b in enumerate(bars)}
        if d not in im:
            continue
        i = im[d]
        mv, px = meta.get(code), last_px.get(code) or bars[-1]["close"]
        if not mv:
            continue
        mcap = mv * bars[i]["close"] / px
        if not (F["min_vol_ratio"] <= vr <= F["max_vol_ratio"]
                and F["min_market_cap"] <= mcap < F["max_market_cap"]
                and chg >= F["min_change_rate"]):
            continue
        kfeat = calc_kline_features(bars[: i + 1])
        if not pass_kline_filter(kfeat, F):
            continue
        out.append({"code": code, "name": name, "date": d, "i": i, "bars": bars,
                    "vr": vr, "kfeat": kfeat, "stored": score, "snap": None})
    return out


def pool_universe(start, end):
    KL = json.loads(Path("/tmp/vs/universe_klines.json").read_text())
    meta = json.loads(Path("/tmp/vs/meta.json").read_text())
    out = []
    for code, bars in KL.items():
        m = meta.get(code)
        if not m or not m.get("issued") or len(bars) < 80:
            continue
        df = pd.DataFrame(bars)
        c, v = df["close"].values, df["volume"].values
        hi250 = df["high"].rolling(250, min_periods=200).max().values
        lo250 = df["low"].rolling(250, min_periods=200).min().values
        v5 = df["volume"].shift(1).rolling(5).mean().values
        for i in range(61, len(bars)):
            d = bars[i]["date"]
            if d < start or d > end or v[i] <= 0 or c[i - 1] <= 0:
                continue
            chg = (c[i] / c[i - 1] - 1) * 100
            if chg < F["min_change_rate"]:
                continue
            mcap = m["issued"] * c[i]
            if not (F["min_market_cap"] <= mcap < F["max_market_cap"]):
                continue
            if not v5[i] or v5[i] != v5[i] or v5[i] <= 0:
                continue
            vr = v[i] / v5[i]
            if not (F["min_vol_ratio"] <= vr <= F["max_vol_ratio"]):
                continue
            h, lo = hi250[i], lo250[i]
            if h != h or h <= 0:
                continue
            h = max(h, c[i])
            if (h - lo) / h < F["min_decline_from_high"] or c[i] / h < F["min_near_high_ratio"]:
                continue
            kfeat = calc_kline_features(df.iloc[: i + 1])
            if not pass_kline_filter(kfeat, F):
                continue
            snap = {"highest52weeks_price": h, "lowest52weeks_price": lo, "last_price": c[i],
                    "change_rate": chg, "volume_ratio": round(vr, 3),
                    "turnover_rate": v[i] / (m["outstanding"] or m["issued"]) * 100}
            out.append({"code": code, "name": m.get("name", code), "date": d, "i": i,
                        "bars": bars, "vr": vr, "kfeat": kfeat, "stored": None, "snap": snap})
    return out


# ───────────────────────── 打分、选股、交易
def score_all(pool, vname):
    fn = VARIANTS[vname]
    for t in pool:
        if t["snap"] is not None:
            ps.score_volume = fn
            try:
                t[vname] = ps.score_pattern(t["snap"], t["kfeat"])["score"]
            finally:
                ps.score_volume = NEW_SCORE_VOLUME
        else:  # 库内分 = 老打分（打分器自首个 commit 未改）；只换维度2
            t[vname] = (t["stored"] - vol_legacy(t["vr"], t["kfeat"])[0]
                        + fn(t["vr"], t["kfeat"])[0])


MIN_SCORE = {}


def select(pool, vname):
    by_day = defaultdict(list)
    for t in pool:
        if t[vname] >= MIN_SCORE.get(vname, F["min_score"]):
            by_day[t["date"]].append(t)
    last, picked = {}, []
    for d in sorted(by_day):
        dd = datetime.strptime(d, "%Y-%m-%d")
        xs = [t for t in by_day[d]
              if t["code"] not in last or (dd - last[t["code"]]).days >= COOLDOWN]
        xs.sort(key=lambda t: t[vname], reverse=True)
        for t in xs[:TOPN]:
            last[t["code"]] = dd
            picked.append(t)
    return picked


def trade(t):
    bars, i = t["bars"], t["i"]
    if i + 1 >= len(bars):
        return None
    entry = bars[i + 1]["open"]
    closes = [float(b["close"]) for b in bars]
    mid = sma(closes, i, 20)
    if entry <= 0 or (MAX_EXT is not None and mid and entry / mid - 1 > MAX_EXT):
        return None
    x = simulate_exit(bars, i + 1, entry, EXIT_CONFIG)
    return {**t, "entry": entry, "r": ret(entry, x["exit_px"]), "hold": x["hold_days"],
            "reason": x["reason"], "xi": i + x["hold_days"]}


def portfolio(trades, slots, cal):
    """固定槽位、复利；每笔占入场前一日净值的 1/slots，按收盘盯市。"""
    eq_cash, pos, curve = 1.0, [], []
    by_entry = defaultdict(list)
    for t in trades:
        by_entry[t["bars"][t["i"] + 1]["date"]].append(t)
    for d in cal:
        for t in sorted(by_entry.get(d, []), key=lambda z: -z["score_used"]):
            if len(pos) >= slots:
                continue
            nav = eq_cash + sum(p["val"] for p in pos)
            alloc = min(nav / slots, eq_cash)
            if alloc <= 0:
                continue
            eq_cash -= alloc
            pos.append({"t": t, "sh": alloc / t["entry"], "val": alloc, "taken": True})
            t["taken"] = True
        keep = []
        for p in pos:
            t = p["t"]
            im = {b["date"]: k for k, b in enumerate(t["bars"])}
            k = im.get(d)
            if k is not None:
                p["val"] = p["sh"] * (t["bars"][k]["close"] if k < t["xi"] else
                                      t["entry"] * (1 + t["r"]) if k == t["xi"] else p["val"] / p["sh"])
            if k is not None and k >= t["xi"]:
                eq_cash += p["sh"] * t["entry"] * (1 + t["r"])
            else:
                keep.append(p)
        pos = keep
        curve.append(eq_cash + sum(p["val"] for p in pos))
    peak, mdd = 1.0, 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return curve[-1] - 1 if curve else 0, mdd


def stats(trades):
    rs = sorted(t["r"] for t in trades)
    n = len(rs)
    if not n:
        return {"n": 0}
    comp = 1.0
    for r in rs:
        comp *= 1 + r
    return {"n": n, "win": sum(r > 0 for r in rs) / n, "mean": sum(rs) / n,
            "med": rs[n // 2] if n % 2 else (rs[n // 2 - 1] + rs[n // 2]) / 2,
            "sum": sum(rs), "worst": rs[0], "best": rs[-1],
            "hold": sum(t["hold"] for t in trades) / n}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", choices=["signals", "universe"], default="signals")
    ap.add_argument("--start", default="2025-09-15")
    ap.add_argument("--end", default="2026-09-30")
    ap.add_argument("--min-score-new", type=int, default=None,
                    help="V1-V3 用的 min_score（默认同线上 60）；base 恒为线上值")
    a = ap.parse_args()
    if a.min_score_new is not None:
        MIN_SCORE.update({v: a.min_score_new for v in VARIANTS if v != "base"})
    pool = pool_signals() if a.pool == "signals" else pool_universe(a.start, a.end)
    print(f"pool={a.pool}  candidates passing initial+kline filter: {len(pool)}  "
          f"days={len({t['date'] for t in pool})}  {min(t['date'] for t in pool)}→{max(t['date'] for t in pool)}")
    cal = sorted({b["date"] for t in pool for b in t["bars"] if b["date"] >= min(t["date"] for t in pool)})
    res, sel = {}, {}
    for v in VARIANTS:
        score_all(pool, v)
    for v in VARIANTS:
        picked = select(pool, v)
        sel[v] = {(t["date"], t["code"]) for t in picked}
        trades = []
        for t in picked:
            x = trade(t)
            if x and x["r"] is not None:
                x["score_used"] = t[v]
                trades.append(x)
        s = stats(trades)
        s["signals"] = len(picked)
        s["pass60"] = sum(t[v] >= MIN_SCORE.get(v, F["min_score"]) for t in pool)
        for slots in (2, 3):
            for t in trades:
                t["taken"] = False
            tot, mdd = portfolio(trades, slots, cal)
            s[f"port{slots}"], s[f"mdd{slots}"] = tot, mdd
            s[f"taken{slots}"] = sum(t["taken"] for t in trades)
        res[v] = s
        res[v]["trades"] = trades
    hdr = (f"{'var':5}{'≥60':>5}{'sig':>5}{'trd':>5}{'win':>7}{'mean':>8}{'med':>8}{'sumR':>9}"
           f"{'worst':>8}{'hold':>6}{'2slot':>9}{'mdd2':>8}{'3slot':>9}{'mdd3':>8}  chg_vs_base")
    print(hdr)
    for v, s in res.items():
        if not s["n"]:
            print(v, "no trades")
            continue
        diff = len(sel[v] - sel["base"])
        print(f"{v:5}{s['pass60']:5}{s['signals']:5}{s['n']:5}{s['win']*100:6.1f}%{s['mean']*100:+7.2f}%"
              f"{s['med']*100:+7.2f}%{s['sum']*100:+8.1f}%{s['worst']*100:+7.1f}%{s['hold']:6.1f}"
              f"{s['port2']*100:+8.1f}%{s['mdd2']*100:+7.1f}%{s['port3']*100:+8.1f}%{s['mdd3']*100:+7.1f}%"
              f"  new={diff} dropped={len(sel['base'] - sel[v])}")
    # 维度2 区分度：池内按新老分档看收益（全体候选，不截断）
    print("\ncontraction_pre 分桶（全体候选，T+1开盘→bb_mid≤20日；不含冷却/前3截断）")
    allx = [x for x in (trade(t) for t in pool) if x and x["r"] is not None]
    for x in allx:
        for v in VARIANTS:
            x[v] = x[v]
    for lo, hi in [(0, .2), (.2, .4), (.4, .6), (.6, 1.01)]:
        xs = [x["r"] for x in allx if lo <= x["kfeat"]["contraction_pre"] < hi]
        if xs:
            print(f"  c_pre {lo:.1f}-{min(hi,1):.1f}: n={len(xs):4} mean={sum(xs)/len(xs)*100:+6.2f}% med={sorted(xs)[len(xs)//2]*100:+6.2f}% "
                  f"win={sum(r>0 for r in xs)/len(xs)*100:5.1f}%")
    for lo, hi in [(2, 2.5), (2.5, 3), (3, 3.5), (3.5, 4.01)]:
        xs = [x["r"] for x in allx if lo <= x["vr"] < hi]
        if xs:
            print(f"  vr {lo}-{min(hi,4)}: n={len(xs):4} mean={sum(xs)/len(xs)*100:+6.2f}% med={sorted(xs)[len(xs)//2]*100:+6.2f}% "
                  f"win={sum(r>0 for r in xs)/len(xs)*100:5.1f}%")
    # 区分度：全体候选 score 与收益的秩相关；新增/剔除的笔；同等入池数（只看排序能力）
    def rank(xs):
        o = sorted(range(len(xs)), key=lambda k: xs[k]); r = [0] * len(xs)
        for j, k in enumerate(o):
            r[k] = j
        return r
    def spearman(a_, b_):
        ra, rb = rank(a_), rank(b_); n = len(a_)
        ma, mb = sum(ra) / n, sum(rb) / n
        cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
        va = sum((x - ma) ** 2 for x in ra) ** .5; vb = sum((y - mb) ** 2 for y in rb) ** .5
        return cov / (va * vb) if va and vb else 0
    def med(xs):
        xs = sorted(xs); n = len(xs)
        return 0 if not n else xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2
    print("\n区分度（全体可交易候选）")
    nbase = res["base"]["pass60"]
    for v in VARIANTS:
        rho = spearman([x[v] for x in allx], [x["r"] for x in allx])
        vol_only = [VARIANTS[v](x["vr"], x["kfeat"])[0] for x in allx]
        rho_d = spearman(vol_only, [x["r"] for x in allx])
        thr = sorted((x[v] for x in pool), reverse=True)[min(nbase, len(pool)) - 1]
        top = [x for x in allx if x[v] >= thr]
        tr = [x["r"] for x in top]
        print(f"  {v:5} rho(score,r)={rho:+.3f}  rho(维度2,r)={rho_d:+.3f}  | 同入池数 阈值={thr} n={len(tr)} "
              f"mean={sum(tr)/len(tr)*100:+.2f}% med={med(tr)*100:+.2f}% win={sum(r>0 for r in tr)/len(tr)*100:.1f}%")
    base_k = {(t["date"], t["code"]) for t in res["base"]["trades"]}
    for v in ("V1", "V2", "V3"):
        added = [t["r"] for t in res[v]["trades"] if (t["date"], t["code"]) not in base_k]
        vk = {(t["date"], t["code"]) for t in res[v]["trades"]}
        dropped = [t["r"] for t in res["base"]["trades"] if (t["date"], t["code"]) not in vk]
        f = lambda xs: (f"n={len(xs)} mean={sum(xs)/len(xs)*100:+.2f}% med={med(xs)*100:+.2f}% "
                        f"win={sum(r>0 for r in xs)/len(xs)*100:.0f}%") if xs else "n=0"
        print(f"  {v} 新增: {f(added)} | 剔除: {f(dropped)}")
    for v in VARIANTS:
        s = res[v]
        print(f"  {v} median={s['med']*100:+.2f}%  2槽实际成交={s['taken2']}  3槽实际成交={s['taken3']}")
    out = ROOT / f"store/vol_score_stats_{a.pool}{'' if a.min_score_new is None else '_ms' + str(a.min_score_new)}.json"
    out.write_text(json.dumps({v: {k: x for k, x in s.items() if k != "trades"} | {
        "picked": sorted(f"{d} {c}" for d, c in sel[v])} for v, s in res.items()},
        ensure_ascii=False, indent=1, default=str))
    print("wrote", out)


if __name__ == "__main__":
    main()
