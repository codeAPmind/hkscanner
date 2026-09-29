"""出场：默认时间出场。棘轮函数仍保留，方便以后对照，默认配置不用。"""


def ratchet_trail(peak_gain: float, tiers: list[tuple[float, float]]) -> float:
    if not tiers:
        return 1.0
    trail = tiers[0][1]
    for threshold, pct in tiers:
        if peak_gain >= threshold:
            trail = pct
    return trail


def describe_exit_rules(cfg: dict) -> str:
    hold = cfg["hold_max_days"]
    lines = [f"入场：信号次日开盘", f"出场：第 {hold} 个交易日收盘"]
    hard = cfg.get("hard_stop_pct")
    if hard:
        lines.append(f"硬止损：入场价 −{hard:.0%}")
    tiers = cfg.get("ratchet_tiers") or []
    if tiers:
        lines.append("动态追踪（从持仓峰值回撤，涨越多越紧）：")
        for i, (th, tr) in enumerate(tiers):
            nxt = tiers[i + 1][0] if i + 1 < len(tiers) else None
            if nxt is None:
                band = f"峰值浮盈 ≥{th:.0%}"
            elif th == 0:
                band = f"峰值浮盈 <{nxt:.0%}"
            else:
                band = f"峰值浮盈 {th:.0%}–{nxt:.0%}"
            lines.append(f"  · {band} → 回撤 {tr:.0%} 卖出")
    return "\n".join(lines)


def simulate_exit(bars: list[dict], entry_idx: int, entry_px: float,
                  cfg: dict) -> dict | None:
    """bars[entry_idx] 是入场日（T+1）。无硬止损/无棘轮时只做到期收盘。"""
    if entry_px <= 0 or entry_idx >= len(bars):
        return None
    hard = cfg.get("hard_stop_pct")
    hold = cfg["hold_max_days"]
    tiers = cfg.get("ratchet_tiers") or []
    peak = entry_px
    last = min(entry_idx + hold, len(bars))
    for j in range(entry_idx, last):
        b = bars[j]
        day = j - entry_idx + 1
        if hard:
            hard_px = entry_px * (1 - hard)
            if b["low"] <= hard_px:
                return {
                    "exit_px": hard_px, "reason": "HARD_STOP",
                    "hold_days": day, "peak": peak,
                }
        if tiers:
            trail = ratchet_trail((peak - entry_px) / entry_px, tiers)
            if peak > entry_px and b["low"] <= peak * (1 - trail):
                return {
                    "exit_px": peak * (1 - trail),
                    "reason": f"RATCHET_{trail:.0%}",
                    "hold_days": day, "peak": peak,
                }
        peak = max(peak, b["high"])
        if day >= hold:
            return {
                "exit_px": b["close"], "reason": "MAX_HOLD",
                "hold_days": day, "peak": peak,
            }
    b = bars[last - 1]
    return {
        "exit_px": b["close"], "reason": "DATA_END",
        "hold_days": last - entry_idx, "peak": peak,
    }
