"""隔周参数对照。达标才改 config.py，否则维持实盘。

自动改必须同时满足：笔数 ≥ 12 且不少于现用的 80%，
中位至少高 5 个点，均值不更差，最差不更深。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import EXIT_CONFIG, FEISHU_WEBHOOK_SUMMARY, FILTER_CONFIG  # noqa: E402
from engine.exits import simulate_exit, sma  # noqa: E402
from engine.preprocess import calc_kline_features  # noqa: E402

COOLDOWN_DAYS = int(FILTER_CONFIG["signal_cooldown_days"])
MAX_PER_DAY = int(FILTER_CONFIG["max_results"])
VR_LO = float(FILTER_CONFIG["min_vol_ratio"])
VR_HI = float(FILTER_CONFIG["max_vol_ratio"])
SKIP = {"HK.02958", "HK.08603"}
MIN_N = 12


def ret(a, b):
    if a is None or b is None or a <= 0:
        return None
    return b / a - 1


def summarize(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    xs = sorted(xs)
    n = len(xs)
    wins = [x for x in xs if x > 0]
    return {
        "n": n,
        "mean": sum(xs) / n,
        "med": xs[n // 2],
        "win": len(wins) / n,
        "worst": xs[0],
    }


def load_mcap(codes):
    from moomoo import RET_OK, OpenQuoteContext
    ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
    meta, last_px = {}, {}
    try:
        for i in range(0, len(codes), 80):
            batch = codes[i:i + 80]
            rc, data = ctx.get_market_snapshot(batch)
            frames = []
            if rc != RET_OK:
                for c in batch:
                    rc2, data2 = ctx.get_market_snapshot([c])
                    if rc2 == RET_OK:
                        frames.append(data2)
            else:
                frames.append(data)
            for df in frames:
                for _, r in df.iterrows():
                    mv, lp = r.get("total_market_val"), r.get("last_price")
                    if mv == mv and mv and mv > 0:
                        meta[r["code"]] = float(mv)
                    if lp == lp and lp and lp > 0:
                        last_px[r["code"]] = float(lp)
    finally:
        ctx.close()
    return meta, last_px


def load_candidates():
    kl = json.loads((ROOT / "store/backtest_klines.json").read_text())
    conn = sqlite3.connect(ROOT / "store/signals.db")
    sig = conn.execute(
        "SELECT code, name, scan_date, score, vol_ratio FROM signals "
        "ORDER BY scan_date, score DESC"
    ).fetchall()
    conn.close()
    codes = sorted({c for c, *_ in sig if c not in SKIP})
    meta, last_px = load_mcap(codes)
    out = []
    last_bar = ""
    for code, name, d, score, vr in sig:
        bars = kl.get(code)
        if not bars:
            continue
        last_bar = max(last_bar, bars[-1]["date"])
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
        closes = [float(b["close"]) for b in bars]
        mid = sma(closes, i, 20)
        out.append({
            "code": code, "date": d, "vr": vr, "ret_1m": feat["ret_1m"],
            "i": i, "bars": bars, "entry": entry,
            "scan_mcap": (today_mv * bars[i]["close"] / px_now) / 1e8,
            "ext": (entry / mid - 1) if mid else None,
        })
    return out, last_bar, len(meta)


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


def exit_cfg(mode: str) -> dict:
    if mode == "bb20":
        return {
            "exit_mode": "bb_mid", "bb_period": 20,
            "hard_stop_pct": None, "hold_max_days": 20, "ratchet_tiers": [],
        }
    return {"hard_stop_pct": None, "hold_max_days": 10, "ratchet_tiers": []}


def evaluate(cands, mcap_lo, min_r1, max_ext, mode):
    pool = []
    for t in cands:
        if t["vr"] is None or not (VR_LO <= t["vr"] <= VR_HI):
            continue
        if not (mcap_lo <= t["scan_mcap"] < 50):
            continue
        if t["ret_1m"] < min_r1:
            continue
        if max_ext is not None and (t["ext"] is None or t["ext"] > max_ext):
            continue
        pool.append(t)
    picked = pick(pool)
    rets = []
    for t in picked:
        x = simulate_exit(t["bars"], t["i"] + 1, t["entry"], exit_cfg(mode))
        if not x:
            continue
        r = ret(t["entry"], x["exit_px"])
        if r is not None:
            rets.append(r)
    return summarize(rets)


def current_spec():
    mode = "bb20" if EXIT_CONFIG.get("exit_mode") == "bb_mid" else "t10"
    return {
        "mcap_lo": FILTER_CONFIG["min_market_cap"] / 1e8,
        "min_r1": float(FILTER_CONFIG["min_ret_1m"] or 0),
        "max_ext": FILTER_CONFIG.get("max_ext_sma20"),
        "mode": mode,
    }


def label(spec) -> str:
    ext = "不限" if spec["max_ext"] is None else f"+{spec['max_ext']:.0%}"
    exit_name = "布林20" if spec["mode"] == "bb20" else "10日"
    return f"{spec['mcap_lo']:.0f}-50亿 / 1月≥{spec['min_r1']:.0%} / 距中轨≤{ext} / {exit_name}"


def pct(x):
    return "—" if x is None else f"{x * 100:+.1f}%"


def row_line(spec, s) -> str:
    if not s:
        return f"- {label(spec)}：无成交"
    return (f"- {label(spec)}：n={s['n']} 中位{pct(s['med'])} "
            f"均{pct(s['mean'])} 胜率{s['win']*100:.0f}% 最差{pct(s['worst'])}")


def same_spec(a, b) -> bool:
    return (
        abs(a["mcap_lo"] - b["mcap_lo"]) < 1e-6
        and abs(a["min_r1"] - b["min_r1"]) < 1e-6
        and a["max_ext"] == b["max_ext"]
        and a["mode"] == b["mode"]
    )


def gate_failures(cur_s, new_s) -> list[str]:
    fails = []
    if new_s["n"] < MIN_N:
        fails.append(f"笔数 {new_s['n']} < {MIN_N}")
    if cur_s["n"] and new_s["n"] < 0.8 * cur_s["n"]:
        fails.append(f"笔数只有现用的 {new_s['n']}/{cur_s['n']}，像是少做几笔做出来的")
    gap = new_s["med"] - cur_s["med"]
    if gap < 0.05:
        fails.append(f"中位只高 {gap * 100:.1f} 个点，不到 5")
    if new_s["mean"] < cur_s["mean"]:
        fails.append(f"均值更差 {pct(new_s['mean'])}，现用 {pct(cur_s['mean'])}")
    if new_s["worst"] < cur_s["worst"]:
        fails.append(f"最差更深 {pct(new_s['worst'])}，现用 {pct(cur_s['worst'])}")
    return fails


def apply_config(spec) -> None:
    hold = 20 if spec["mode"] == "bb20" else 10
    cool = 28 if hold == 20 else 14
    if spec["mode"] == "bb20":
        exit_block = (
            "# 出场：现价回到 20 日均线（昨收 SMA，布林中轨）；最多拿 20 个交易日。\n"
            "EXIT_CONFIG = {\n"
            '    "exit_mode": "bb_mid",\n'
            '    "bb_period": 20,\n'
            '    "hard_stop_pct": None,\n'
            '    "hold_max_days": 20,\n'
            '    "ratchet_tiers": [],\n'
            "}"
        )
    else:
        exit_block = (
            "# 出场：次日开盘买，第 10 个交易日收盘卖。\n"
            "EXIT_CONFIG = {\n"
            '    "hard_stop_pct": None,\n'
            '    "hold_max_days": 10,\n'
            '    "ratchet_tiers": [],\n'
            "}"
        )
    for path in (ROOT / "config.py", ROOT / "config.example.py"):
        text = path.read_text()
        text = re.sub(
            r'"min_market_cap":\s*[0-9.eE+-]+',
            f'"min_market_cap": {spec["mcap_lo"]:.0f}e8',
            text, count=1,
        )
        text = re.sub(
            r'"min_ret_1m":\s*[0-9.]+',
            f'"min_ret_1m": {spec["min_r1"]:.2f}',
            text, count=1,
        )
        text = re.sub(
            r'"max_ext_sma20":\s*[0-9.]+',
            f'"max_ext_sma20": {spec["max_ext"]:.2f}',
            text, count=1,
        )
        text = re.sub(
            r'"signal_cooldown_days":\s*\d+',
            f'"signal_cooldown_days": {cool}',
            text, count=1,
        )
        text = re.sub(
            r"# 出场：.*?^EXIT_CONFIG = \{.*?\n\}",
            exit_block,
            text, count=1, flags=re.S | re.M,
        )
        if f'"hold_max_days": {hold}' not in text:
            raise RuntimeError(f"failed to write exit block in {path.name}")
        path.write_text(text)


def send_card(title: str, body: str, template: str) -> None:
    webhook = FEISHU_WEBHOOK_SUMMARY
    if not webhook:
        print("[sweep] no webhook")
        return
    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": title},
                       "template": template},
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": body}},
                {"tag": "note", "elements": [{"tag": "plain_text",
                    "content": "中位、均值、最差同时更好才改实盘。不构成投资建议。"}]},
            ],
        },
    }
    req = urllib.request.Request(
        webhook, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    urllib.request.urlopen(req, timeout=10)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-refresh", action="store_true")
    args = ap.parse_args()

    if not args.no_refresh:
        from store.backtest_prices import refresh_tails
        refresh_tails()

    cands, last_bar, n_mcap = load_candidates()
    cur = current_spec()
    grid = []
    for mcap_lo in (5.0, 20.0):
        for min_r1 in (0.05, 0.08, 0.12):
            for max_ext in (0.30, 0.40, 0.60):
                for mode in ("t10", "bb20"):
                    spec = {
                        "mcap_lo": mcap_lo, "min_r1": min_r1,
                        "max_ext": max_ext, "mode": mode,
                    }
                    s = evaluate(cands, mcap_lo, min_r1, max_ext, mode)
                    grid.append((spec, s))

    cur_stats = evaluate(cands, cur["mcap_lo"], cur["min_r1"], cur["max_ext"], cur["mode"])
    eligible = [(sp, s) for sp, s in grid if s and s["n"] >= MIN_N]
    eligible.sort(key=lambda x: (x[1]["med"], x[1]["mean"]), reverse=True)

    lines = [
        f"信号日市值重估，K线截至 **{last_bar or '—'}**，快照市值 {n_mcap} 只。",
        f"冷却 {COOLDOWN_DAYS} 天，每天最多 {MAX_PER_DAY} 只。笔数 < {MIN_N} 不参与排序。",
        "",
        "**当前**",
        row_line(cur, cur_stats),
        "",
        "**中位更好的前 5 组**（仍可能是拟合）",
    ]
    if not eligible:
        lines.append("- 没有一组达到最少笔数")
    else:
        for sp, s in eligible[:5]:
            lines.append(row_line(sp, s))

    template = "blue"
    advice = "未改实盘。没有一组同时把中位、均值、最差都做更好。"
    chosen = None
    if cur_stats:
        passed = []
        for sp, s in eligible:
            if same_spec(sp, cur):
                continue
            if not gate_failures(cur_stats, s):
                passed.append((sp, s))
        passed.sort(key=lambda x: (x[1]["med"], x[1]["mean"], x[1]["worst"]), reverse=True)
        if passed:
            chosen = passed[0]
            advice = f"已改实盘为 {label(chosen[0])}。"
            template = "green"
        elif eligible:
            leader, lead_s = eligible[0]
            if same_spec(leader, cur):
                advice = "未改实盘。中位最高的就是现用这组。"
            else:
                why = "；".join(gate_failures(cur_stats, lead_s))
                advice = f"未改实盘。中位最高的是 {label(leader)}，但 {why}。"
    lines += [
        "",
        "**自动改条件** 笔数≥12 且不少于现用的 80%，中位至少高 5 个点，均值不更差，最差不更深。",
        f"**结论** {advice}",
    ]
    body = "\n".join(lines)
    title = f"hkscanner 参数对照 · {datetime.now():%Y-%m-%d}"
    print(body)

    out = ROOT / "store" / "reports"
    out.mkdir(exist_ok=True)
    (out / f"param_sweep_{datetime.now():%Y-%m-%d}.md").write_text(
        f"# {title}\n\n{body}\n", encoding="utf-8",
    )
    if chosen and not args.dry_run:
        apply_config(chosen[0])
        print("[sweep] config updated")
    elif chosen:
        print("[sweep] dry-run, would update config")
    if args.dry_run:
        print("[sweep] dry-run, skip feishu")
        return
    send_card(title, body, template)
    print("[sweep] feishu sent")


if __name__ == "__main__":
    main()
