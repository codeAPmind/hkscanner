import pandas as pd


def _as_frame(df) -> pd.DataFrame | None:
    if df is None:
        return None
    if isinstance(df, pd.DataFrame):
        return df
    if isinstance(df, list) and df:
        return pd.DataFrame(df)
    return None


def calc_kline_features(df: pd.DataFrame) -> dict:
    """
    快照里没有的特征：
      - contraction_ratio：近15日（含当日）量 < 60日均量 60% 的占比
      - contraction_pre：同上，但剔除当日（洗盘后再反转）
      - ret_1m：截至昨收的约 21 个交易日收益（短线反转）
      - ret_12_1：剔除近 1 个月后的约 12 个月收益（中间动量）
      - ma_bullish：均线多头排列
    快照里已有：52w高低、量比、换手率、涨幅 → 不重复算
    """
    empty = {
        "contraction_ratio": 0.0,
        "contraction_pre": 0.0,
        "ma_bullish": False,
        "ret_1m": None,
        "ret_12_1": None,
        "sma20": None,
        "ext_sma20": None,
    }
    df = _as_frame(df)
    if df is None or len(df) < 20:
        return empty

    close = df["close"]
    volume = df["volume"]

    vol_ma60 = volume.rolling(60).mean() if len(df) >= 60 else volume.expanding().mean()
    base_vol = float(vol_ma60.iloc[-1] or 0)
    recent_vol = volume.tail(15)
    contraction_ratio = float((recent_vol < base_vol * 0.6).mean()) if base_vol > 0 else 0.0

    if len(df) >= 16:
        base_pre = float(vol_ma60.iloc[-2] or 0)
        pre_vol = volume.iloc[-16:-1]
        contraction_pre = float((pre_vol < base_pre * 0.6).mean()) if base_pre > 0 else 0.0
    else:
        contraction_pre = contraction_ratio

    ret_1m = None
    if len(df) >= 22:
        a = float(close.iloc[-22])
        b = float(close.iloc[-2])
        if a > 0 and b > 0:
            ret_1m = b / a - 1.0

    ret_12_1 = None
    if len(df) >= 252:
        a = float(close.iloc[-252])
        b = float(close.iloc[-22])
        if a > 0 and b > 0:
            ret_12_1 = b / a - 1.0

    ma5 = close.rolling(5).mean().iloc[-1]
    ma10 = close.rolling(10).mean().iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1]
    ma_bullish = bool(ma5 > ma10 > ma20)

    sma20 = float(ma20) if ma20 == ma20 and ma20 and ma20 > 0 else None
    last_c = float(close.iloc[-1])
    ext_sma20 = (last_c / sma20 - 1.0) if sma20 and last_c > 0 else None

    return {
        "contraction_ratio": round(contraction_ratio, 3),
        "contraction_pre": round(contraction_pre, 3),
        "ma_bullish": ma_bullish,
        "ret_1m": None if ret_1m is None else round(ret_1m, 4),
        "ret_12_1": None if ret_12_1 is None else round(ret_12_1, 4),
        "sma20": None if sma20 is None else round(sma20, 4),
        "ext_sma20": None if ext_sma20 is None else round(ext_sma20, 4),
    }
