import time
import pandas as pd
from datetime import datetime, timedelta
from moomoo import KLType, AuType, KL_FIELD, RET_OK

KLINE_SLEEP = 1.0    # 30次/30s → 每次1s，留余量
KLINE_DAYS  = 65     # 约3个月


def fetch_klines_for_candidates(ctx, codes: list[str],
                                cache) -> dict[str, pd.DataFrame]:
    """
    逐只拉历史K线，有缓存则跳过（节省月度 Quota）。
    返回 {code: DataFrame}
    """
    end   = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=100)).strftime("%Y-%m-%d")
    results = {}

    for i, code in enumerate(codes):
        # 优先读缓存
        cached = cache.get(code)
        if cached is not None:
            results[code] = cached
            print(f"  [{i + 1}/{len(codes)}] {code} 命中缓存")
            continue

        ret, data, _ = ctx.request_history_kline(
            code,
            start=start,
            end=end,
            ktype=KLType.K_DAY,
            autype=AuType.QFQ,
            fields=[
                KL_FIELD.DATE_TIME,
                KL_FIELD.OPEN, KL_FIELD.CLOSE,
                KL_FIELD.HIGH, KL_FIELD.LOW,
                KL_FIELD.TRADE_VOL, KL_FIELD.TRADE_VAL,
                KL_FIELD.CHANGE_RATE, KL_FIELD.TURNOVER_RATE,
            ],
            max_count=KLINE_DAYS
        )

        if ret == RET_OK and not data.empty:
            results[code] = data
            cache.set(code, data)
            print(f"  [{i + 1}/{len(codes)}] {code} 拉取成功 ({len(data)} 根K线)")
        else:
            print(f"  [{i + 1}/{len(codes)}] {code} 失败：{data}")

        if i < len(codes) - 1:
            time.sleep(KLINE_SLEEP)

    print(f"K线完成：{len(results)}/{len(codes)} 只")
    return results
