import pandas as pd


def calc_kline_features(df: pd.DataFrame) -> dict:
    """
    只计算快照里没有的特征：
      - contraction_ratio：底部缩量程度
      - ma_bullish：均线多头排列
    快照里已有：52w高低、量比、换手率、涨幅 → 不重复算
    """
    if df is None or len(df) < 20:
        return {"contraction_ratio": 0, "ma_bullish": False}

    close  = df['close']
    volume = df['volume']

    vol_ma60 = volume.rolling(60).mean() if len(df) >= 60 else volume.expanding().mean()
    base_vol = vol_ma60.iloc[-1]

    # 近15日中有多少天量 < 60日均量的60%（越高说明洗盘越彻底）
    recent_vol        = volume.tail(15)
    contraction_ratio = float((recent_vol < base_vol * 0.6).mean()) if base_vol > 0 else 0

    # 均线多头
    ma5  = close.rolling(5).mean().iloc[-1]
    ma10 = close.rolling(10).mean().iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1]
    ma_bullish = bool(ma5 > ma10 > ma20)

    return {
        "contraction_ratio": round(contraction_ratio, 3),
        "ma_bullish":        ma_bullish,
    }
