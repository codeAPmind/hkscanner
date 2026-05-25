def score_pattern(snap: dict, kfeat: dict) -> dict:
    """
    snap:  快照字段（直接用 Futu 字段名）
    kfeat: calc_kline_features 的衍生特征
    """
    score      = 0
    signals    = []
    risk_flags = []

    high52w   = snap.get('highest52weeks_price', 0)
    low52w    = snap.get('lowest52weeks_price', 0)
    price     = snap.get('last_price', 0)
    change    = snap.get('change_rate', 0)
    vol_ratio = snap.get('volume_ratio', 1)
    turnover  = snap.get('turnover_rate', 0)

    contraction = kfeat.get('contraction_ratio', 0)
    ma_bullish  = kfeat.get('ma_bullish', False)

    if high52w <= 0:
        return {"score": 0, "stage": "数据异常", "stage_en": "ERROR",
                "signals": [], "risk_flags": ["高点数据异常"]}

    decline   = (high52w - low52w) / high52w
    near_high = price / high52w

    # ── 维度1：前期回撤深度（20分）
    if decline > 0.50:
        score += 20
        signals.append(("✅", "深度洗盘", f"回撤 {decline:.0%}，筹码清洗充分"))
    elif decline > 0.40:
        score += 15
        signals.append(("✅", "充分回撤", f"回撤 {decline:.0%}"))
    elif decline > 0.25:
        score += 8
        signals.append(("⚠️", "回撤偏浅", f"回撤 {decline:.0%}，洗盘不彻底"))
    else:
        signals.append(("❌", "回撤不足", f"回撤 {decline:.0%}"))

    # ── 维度2：量比（30分，最关键）
    if vol_ratio < 0.8:
        score += 30
        signals.append(("✅", "极度缩量突破 [最强]", f"量比 {vol_ratio}，浮筹已清"))
    elif vol_ratio < 1.2:
        score += 22
        signals.append(("✅", "缩量/温和放量", f"量比 {vol_ratio}，筹码锁定好"))
    elif vol_ratio < 2.0:
        score += 12
        signals.append(("⚠️", "正常放量", f"量比 {vol_ratio}，关注次日缩量确认"))
    elif vol_ratio < 3.5:
        score += 5
        signals.append(("⚠️", "量比偏高", f"量比 {vol_ratio}，注意抛压"))
    else:
        risk_flags.append(f"⚠ 巨量 量比={vol_ratio}，警惕借机出货")

    # ── 维度3：今日涨幅（25分）
    if change > 25:
        score += 25
        signals.append(("✅", "主升浪大阳线", f"+{change:.1f}%"))
    elif change > 15:
        score += 20
        signals.append(("✅", "大阳线反转", f"+{change:.1f}%"))
    elif change > 8:
        score += 12
        signals.append(("✅", "有效突破", f"+{change:.1f}%"))
    elif change > 4:
        score += 6
        signals.append(("⚠️", "涨幅偏小", f"+{change:.1f}%"))
    else:
        signals.append(("❌", "涨幅不足", f"+{change:.1f}%"))

    # ── 维度4：突破前高位置（15分）
    if near_high > 0.98:
        score += 15
        signals.append(("✅", "突破/创52周新高", f"当前={near_high:.0%}前高，上方无压"))
    elif near_high > 0.90:
        score += 10
        signals.append(("✅", "接近前高", f"当前={near_high:.0%}前高"))
    elif near_high > 0.75:
        score += 5
        signals.append(("⚠️", "距前高尚远", f"当前={near_high:.0%}前高"))
    else:
        signals.append(("❌", "远离前高", f"当前={near_high:.0%}前高"))

    # ── 维度5：换手率（10分）
    if turnover < 2.0:
        score += 10
        signals.append(("✅", "低换手，筹码稳", f"换手 {turnover:.2f}%"))
    elif turnover < 5.0:
        score += 6
        signals.append(("✅", "换手适中", f"换手 {turnover:.2f}%"))
    elif turnover < 12.0:
        score += 2
        signals.append(("⚠️", "换手偏高", f"换手 {turnover:.2f}%"))
    else:
        risk_flags.append(f"⚠ 换手 {turnover:.1f}% 过高，警惕出货")

    # ── 加分：均线多头（+5）
    if ma_bullish:
        score += 5
        signals.append(("✅", "均线多头排列", "5日>10日>20日"))

    # ── 加分：底部深度缩量（+5）
    if contraction > 0.7:
        score += 5
        signals.append(("✅", "底部深度缩量", f"近15日 {contraction:.0%} 时间量<60日均量"))

    score = min(100, score)

    # 阶段判断
    if change > 20 and vol_ratio >= 1.0:
        stage, stage_en = "主升浪加速 🚀", "MARKUP_ACCEL"
    elif change > 10 and near_high > 0.90:
        stage, stage_en = "拉升确认 📈", "MARKUP"
    elif change > 8:
        stage, stage_en = "反转启动 ⚡", "REVERSAL"
    elif change > 3:
        stage, stage_en = "洗盘末期 ⏳", "LATE_WASHOUT"
    else:
        stage, stage_en = "洗盘整理 😴", "WASHOUT"

    return {
        "score":      score,
        "stage":      stage,
        "stage_en":   stage_en,
        "signals":    signals,
        "risk_flags": risk_flags,
    }
