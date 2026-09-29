"""用 yfinance 拉信号股日线，不碰 Futu 历史K线配额。

符号映射与批量下载口径对齐 YiDong_AutoTrader_H/scripts/hk_universe_fetch.py。
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

SIGNALS_DB = Path("/Users/openclaw/openclaw_workspace/hkscanner/store/signals.db")
OUT = Path("/Users/openclaw/openclaw_workspace/hkscanner/store/backtest_klines.json")
START = "2026-05-01"
END = "2026-09-25"
BATCH = 40
SLEEP = 1.0
HSI = "^HSI"


def futu_to_yf(code: str) -> str:
    """HK.00700 -> 0700.HK"""
    n = code.split(".")[-1].lstrip("0") or "0"
    return (n.zfill(4) if len(n) <= 4 else n) + ".HK"


def yf_to_futu(sym: str) -> str:
    """0700.HK -> HK.00700"""
    n = sym.replace(".HK", "")
    return "HK." + (n.zfill(5) if len(n) <= 5 else n)


def load_codes() -> list[str]:
    conn = sqlite3.connect(SIGNALS_DB)
    codes = [r[0] for r in conn.execute("SELECT DISTINCT code FROM signals")]
    conn.close()
    return codes


def _batched(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def parse_batch(df, syms: list[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    if df is None or df.empty:
        return out
    for sym in syms:
        try:
            sub = df[sym] if isinstance(df.columns, pd.MultiIndex) else df
        except KeyError:
            continue
        sub = sub.dropna(subset=["Close"])
        if sub.empty:
            continue
        rows = []
        for idx, r in sub.iterrows():
            c = float(r["Close"])
            if c <= 0:
                continue
            rows.append({
                "date": idx.strftime("%Y-%m-%d"),
                "open": float(r["Open"]),
                "high": float(r["High"]),
                "low": float(r["Low"]),
                "close": c,
                "volume": float(r.get("Volume", 0) or 0),
            })
        if rows:
            key = HSI if sym == HSI else yf_to_futu(sym)
            out[key] = rows
    return out


def main():
    cached = json.loads(OUT.read_text()) if OUT.exists() else {}
    codes = load_codes()
    missing = [c for c in codes if c not in cached or len(cached[c]) < 40]
    print(f"signals {len(codes)} unique, missing {len(missing)}, cached {len(cached)}")

    if HSI not in cached:
        missing_syms = [HSI]
    else:
        missing_syms = []
    missing_syms += [futu_to_yf(c) for c in missing]

    for bi, chunk in enumerate(_batched(missing_syms, BATCH), 1):
        print(f"batch {bi} size={len(chunk)}")
        try:
            df = yf.download(
                chunk,
                start=START,
                end=END,
                interval="1d",
                auto_adjust=False,
                progress=False,
                threads=True,
                group_by="ticker",
            )
        except Exception as e:
            print(f"  FAIL {e}")
            time.sleep(SLEEP * 4)
            continue
        parsed = parse_batch(df, chunk)
        cached.update(parsed)
        print(f"  got {len(parsed)} / {len(chunk)}")
        OUT.write_text(json.dumps(cached))
        time.sleep(SLEEP)

    still = [c for c in codes if c not in cached]
    print(f"saved {len(cached)} keys -> {OUT}")
    print(f"still missing {len(still)}: {still[:15]}")


if __name__ == "__main__":
    main()
