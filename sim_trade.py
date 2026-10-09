"""工作日 ~14:10 模拟买卖：先按布林中轨/到期平仓，再买上一交易日扫描结果。

下单走 YiDong HKFutuExecutor（OpenD 11112，环境由 HKSCANNER_TRADE_ENV 决定）。
成交后写入 YiDong 共享持仓表 channel=HKSCANNER，避免次日 recover / 15:55 EOD 抢平。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
YIDONG = Path(os.getenv(
    "YIDONG_ROOT",
    "/Users/openclaw/openclaw_workspace/YiDong_AutoTrader_H",
))
os.chdir(ROOT)
if str(YIDONG) not in sys.path:
    sys.path.insert(0, str(YIDONG))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import EXIT_CONFIG, FEISHU_WEBHOOK_SUMMARY, FILTER_CONFIG, TRADE_CONFIG  # noqa: E402
from notify.feishu import push_trade_card  # noqa: E402
from store.paper_db import PaperDB, weekdays_inclusive  # noqa: E402
from store.signal_db import SignalDB  # noqa: E402

CHANNEL = TRADE_CONFIG["channel"]


def _lot_qty(slot_hkd: float, price: float, lot: int, max_lot_hkd: float = 80_000) -> int:
    """槽位向下取整手数；一手贵过槽位时仍买一手（模拟账户有钱）。"""
    if price <= 0 or lot <= 0:
        return 0
    lot_cost = price * lot
    if lot_cost > max_lot_hkd:
        return 0
    n_lots = int(slot_hkd // lot_cost)
    if n_lots < 1:
        n_lots = 1
    return n_lots * lot


def _ref_buy(snap: dict) -> float:
    ask = float(snap.get("ask_price", 0) or 0)
    last = float(snap.get("last_price", 0) or 0)
    return ask if ask > 0 else last


def _ref_sell(snap: dict) -> float:
    bid = float(snap.get("bid_price", 0) or 0)
    last = float(snap.get("last_price", 0) or 0)
    return bid if bid > 0 else last


def _wait_fills(events: dict, n: int, timeout: float) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if len(events) >= n:
            return
        time.sleep(0.4)


def _broker_codes(executor) -> set[str]:
    try:
        return {
            str(p["code"])
            for p in (executor.get_positions() or [])
            if int(p.get("qty", 0) or 0) > 0
        }
    except Exception as e:
        print(f"[trade] get_positions failed: {e}")
        return set()


def _prior_sma(code: str, period: int = 20) -> float | None:
    """昨收为止的 SMA。今日未完成 K 线不计入。"""
    try:
        from auto_trade.broker_sdk import AuType, KLType, OpenQuoteContext, RET_OK
        from auto_trade.config import FUTU_HOST, FUTU_PORT
    except ImportError:
        return None
    today = date.today().strftime("%Y-%m-%d")
    ctx = OpenQuoteContext(host=FUTU_HOST, port=FUTU_PORT)
    try:
        ret, data, _ = ctx.request_history_kline(
            code,
            ktype=KLType.K_DAY,
            autype=AuType.QFQ,
            max_count=period + 8,
        )
        if ret != RET_OK or data is None or getattr(data, "empty", True):
            return None
        closes = []
        for _, row in data.iterrows():
            raw = row.get("time_key", row.get("date_time", row.get("date", "")))
            dt = str(raw)[:10]
            if not dt or dt >= today:
                continue
            px = float(row.get("close", 0) or 0)
            if px > 0:
                closes.append(px)
        if len(closes) < period:
            return None
        return sum(closes[-period:]) / period
    except Exception as e:
        print(f"[trade] sma {code} failed: {e}")
        return None
    finally:
        ctx.close()


def _entry_open(snap: dict) -> float:
    opn = float(snap.get("open_price", 0) or 0)
    last = float(snap.get("last_price", 0) or 0)
    return opn if opn > 0 else last


def too_extended(code: str, px: float) -> tuple[bool, str]:
    """次日开盘相对昨收 SMA20 超过阈值则不买。"""
    cap = FILTER_CONFIG.get("max_ext_sma20")
    if cap is None or px <= 0:
        return False, ""
    period = int(EXIT_CONFIG.get("bb_period") or 20)
    mid = _prior_sma(code, period)
    if not mid:
        return True, "无昨收均线，跳过"
    ext = px / mid - 1.0
    if ext > cap:
        return True, f"过热 开盘相对中轨 {ext:+.0%} > +{cap:.0%} (px={px:.3f} mid={mid:.3f})"
    return False, f"距中轨 {ext:+.0%}"


def collect_due(paper: PaperDB, today: date, last_px: dict[str, float] | None = None) -> list[dict]:
    hold_days = int(TRADE_CONFIG["hold_max_days"] or EXIT_CONFIG["hold_max_days"])
    mode = EXIT_CONFIG.get("exit_mode") or "time"
    period = int(EXIT_CONFIG.get("bb_period") or 20)
    out = []
    for pos in paper.open_positions():
        try:
            entry = date.fromisoformat(pos["entry_date"])
        except (TypeError, ValueError):
            continue
        held = weekdays_inclusive(entry, today)
        reason = None
        if mode == "bb_mid" and last_px is not None:
            mid = _prior_sma(pos["code"], period)
            px = last_px.get(pos["code"])
            if mid and px and px <= mid:
                reason = f"BB_MID last={px:.3f} mid={mid:.3f}"
        if held >= hold_days:
            reason = reason or "MAX_HOLD"
        if reason:
            row = dict(pos)
            row["held_days"] = held
            row["exit_reason"] = reason
            out.append(row)
    return out


def run(dry_run: bool = False) -> None:
    today = date.today()
    today_s = today.strftime("%Y-%m-%d")
    hold_days = int(TRADE_CONFIG["hold_max_days"] or EXIT_CONFIG["hold_max_days"])
    max_pos = int(TRADE_CONFIG["max_positions"])
    slot_hkd = float(TRADE_CONFIG["slot_hkd"])
    stale = int(TRADE_CONFIG["signal_stale_days"])
    wait_sec = float(TRADE_CONFIG["fill_wait_sec"])

    paper = PaperDB(ROOT / "store" / "paper_positions.db")
    os.environ.setdefault("HKSCANNER_PAPER_DB", str(paper.path))
    sigdb = SignalDB(str(ROOT / "store" / "signals.db"))

    due = paper.due_exits(today, hold_days) if (EXIT_CONFIG.get("exit_mode") or "time") != "bb_mid" else collect_due(paper, today)
    scan_date = sigdb.latest_scan_before(today_s)
    signals = sigdb.signals_on(scan_date) if scan_date else []
    if scan_date:
        age = (today - date.fromisoformat(scan_date)).days
        if age > stale:
            print(f"[trade] scan {scan_date} stale ({age}d > {stale}), skip buys")
            signals = []

    print(
        f"[trade] {datetime.now():%Y-%m-%d %H:%M:%S} env={os.getenv('HK_TRADE_ENV','?')} "
        f"dry={dry_run} open={paper.open_count()} due_exits={len(due)} "
        f"scan={scan_date} signals={len(signals)} slots={max_pos}x{slot_hkd:.0f}"
    )

    lines: list[str] = []
    if dry_run:
        for pos in due:
            lines.append(
                f"SELL {pos['code']} qty={pos['qty']} held={pos.get('held_days')}d "
                f"entry={pos['entry_px']}"
            )
        held = {p["code"] for p in paper.open_positions()}
        free = max_pos - (paper.open_count() - len(due))
        for s in signals:
            if free <= 0:
                break
            if s["code"] in held:
                continue
            lines.append(f"BUY {s['code']} {s['name']} score={s['score']} scan={scan_date}")
            free -= 1
        msg = "dry-run\n" + ("\n".join(lines) or "nothing to do")
        print(msg)
        if lines:
            push_trade_card(today_s, msg, FEISHU_WEBHOOK_SUMMARY)
        return

    from auto_trade.auto_trader.futu_executor import HKFutuExecutor
    from auto_trade.auto_trader.position_manager import open_position, reduce_position
    from auto_trade.config import FUTU_PORT, HK_TRADE_ENV, TRADING_ACCOUNT_HK

    print(
        f"[trade] YiDong executor port={FUTU_PORT} acc={TRADING_ACCOUNT_HK} "
        f"HK_TRADE_ENV={HK_TRADE_ENV}"
    )
    executor = HKFutuExecutor()
    try:
        occupied = _broker_codes(executor)
        last_px = {}
        for pos in paper.open_positions():
            snap = executor.get_snapshot(pos["code"])
            last_px[pos["code"]] = _ref_sell(snap)
        due = collect_due(paper, today, last_px)

        # 1) 先卖到期仓，腾槽位
        sell_fills: dict[str, tuple] = {}
        sell_orders = []
        for pos in due:
            code = pos["code"]
            qty = int(pos["qty"] or 0)
            snap = executor.get_snapshot(code)
            ref = _ref_sell(snap)
            if qty <= 0 or ref <= 0:
                print(f"[trade] skip sell {code}: qty={qty} ref={ref} snap={snap}")
                continue

            def _on_sell(oid, filled_qty, filled_px, *, _pos=pos):
                sell_fills[_pos["code"]] = (filled_qty, filled_px, oid)
                try:
                    reduce_position(_pos["code"], filled_qty, filled_px)
                except Exception as e:
                    print(f"[trade] YiDong reduce_position {_pos['code']} failed: {e}")
                paper.record_close(
                    _pos["id"], today_s, filled_px, filled_qty, oid,
                )

            oid = executor.place_sell_order(
                code, qty, ref,
                purpose="BB_MID" if "BB_MID" in str(pos.get("exit_reason", "")) else "TIME_EXIT",
                slippage=-0.005,
                on_fill=_on_sell,
                channel=CHANNEL,
            )
            sell_orders.append((code, oid))
            print(f"[trade] SELL {code} qty={qty} ref={ref:.3f} order={oid}")
            lines.append(f"卖 {code} qty={qty} 限价参考 {ref:.3f} 单号 {oid or '失败'}")

        if sell_orders:
            _wait_fills(sell_fills, len([x for x in sell_orders if x[1]]), wait_sec)

        occupied = _broker_codes(executor)
        open_now = paper.open_positions()
        held = {p["code"] for p in open_now}
        free = max(0, max_pos - len(open_now))

        # 2) 买上一交易日扫描，按得分
        buy_fills: dict[str, tuple] = {}
        buy_orders = []
        for s in signals:
            if free <= 0:
                break
            code = s["code"]
            if code in held or code in occupied:
                print(f"[trade] skip buy {code}: already held/broker")
                continue
            snap = executor.get_snapshot(code)
            if snap.get("suspension") or snap.get("halted"):
                print(f"[trade] skip buy {code}: halted")
                continue
            ref = _ref_buy(snap)
            open_px = _entry_open(snap)
            skip, why = too_extended(code, open_px)
            if skip:
                print(f"[trade] skip buy {code}: {why}")
                lines.append(f"跳过 {code}：{why}")
                continue
            lot = int(snap.get("lot_size", 100) or 100)
            qty = _lot_qty(slot_hkd, ref, lot)
            if qty <= 0:
                print(f"[trade] skip buy {code}: 1 lot too large @ {ref} lot={lot}")
                lines.append(f"跳过 {code}：一手约 {ref * lot:.0f} 超过上限")
                continue

            def _on_buy(oid, filled_qty, filled_px, *, _s=s):
                buy_fills[_s["code"]] = (filled_qty, filled_px, oid)
                try:
                    open_position(
                        code=_s["code"],
                        qty=filled_qty,
                        entry_price=filled_px,
                        hard_stop_price=0.0,
                        hard_stop_order_id="",
                        signal={
                            "channel": CHANNEL,
                            "scan_date": _s["scan_date"],
                            "score": _s.get("score"),
                        },
                    )
                except Exception as e:
                    print(f"[trade] YiDong open_position {_s['code']} failed: {e}")
                paper.record_open(
                    _s["code"], _s["name"], _s["scan_date"], today_s,
                    filled_qty, filled_px, oid,
                )

            oid = executor.place_entry_order(
                code, qty, ref, on_fill=_on_buy, channel=CHANNEL,
            )
            buy_orders.append((code, oid))
            free -= 1
            held.add(code)
            print(f"[trade] BUY {code} qty={qty} ref={ref:.3f} order={oid} {why}")
            lines.append(
                f"买 {s['name']} {code} qty={qty} 参考 {ref:.3f} {why} "
                f"得分{s['score']} 信号{s['scan_date']} 单号 {oid or '失败'}"
            )

        if buy_orders:
            _wait_fills(buy_fills, len([x for x in buy_orders if x[1]]), wait_sec)

        for code, oid in sell_orders:
            if oid and code not in sell_fills:
                lines.append(f"⚠ 卖单未成交 {code} {oid}（DAY 单留到收盘）")
        for code, oid in buy_orders:
            if oid and code not in buy_fills:
                lines.append(f"⚠ 买单未成交 {code} {oid}（DAY 单留到收盘）")

        book = paper.open_positions()
        if book:
            lines.append("持仓 " + ", ".join(
                f"{p['code']}x{p['qty']}@{p['entry_px']:.3f}({p['entry_date']})"
                for p in book
            ))
        summary = "\n".join(lines) or "今日无买卖"
        print("[trade] done\n" + summary)
        if lines:
            push_trade_card(today_s, summary, FEISHU_WEBHOOK_SUMMARY)
    finally:
        try:
            executor.close()
        except Exception:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    dry = args.dry_run or os.getenv("HKSCANNER_TRADE_DRY_RUN", "").strip() in {
        "1", "true", "yes",
    }
    if os.getenv("HKSCANNER_TRADE_ENABLED", "1").strip().lower() in {"0", "false", "no"}:
        print("[trade] HKSCANNER_TRADE_ENABLED=0, skip")
        sys.exit(0)
    run(dry_run=dry)
