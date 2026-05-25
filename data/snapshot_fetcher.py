import time
import pandas as pd
from moomoo import RET_OK

SNAPSHOT_BATCH = 200    # 每批最多200只
SNAPSHOT_SLEEP = 0.5    # 60次/30s → 每次0.5s，均匀打满但不超限


def fetch_snapshots(ctx, codes: list[str]) -> pd.DataFrame:
    """分批拉快照，约 1800 只只需 5 秒"""
    batches = [codes[i:i + SNAPSHOT_BATCH]
               for i in range(0, len(codes), SNAPSHOT_BATCH)]
    rows = []

    for i, batch in enumerate(batches):
        ret, data = ctx.get_market_snapshot(batch)
        if ret == RET_OK:
            rows.append(data)
        else:
            print(f"  快照批次 {i + 1} 失败：{data}")
        if i < len(batches) - 1:
            time.sleep(SNAPSHOT_SLEEP)

    df = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()

    # SDK 未直接提供 change_rate，从收盘价和昨收价计算
    if not df.empty and 'change_rate' not in df.columns:
        df['change_rate'] = (
            (df['last_price'] - df['prev_close_price'])
            / df['prev_close_price'].replace(0, float('nan'))
            * 100
        ).fillna(0)

    print(f"快照完成：{len(df)} 只")
    return df


def initial_filter(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """利用快照字段初筛，不需要K线"""
    if df.empty:
        return df

    df = df[df['suspension'] == False].copy()
    df = df[df['total_market_val'] >= cfg['min_market_cap']]
    df = df[df['change_rate']      >= cfg['min_change_rate']]
    df = df[df['volume_ratio']     <= cfg['max_vol_ratio']]

    # 52周回撤必须足够深（说明经历过真实洗盘）
    df['decline_from_high'] = (
        (df['highest52weeks_price'] - df['lowest52weeks_price'])
        / df['highest52weeks_price']
    )
    df = df[df['decline_from_high'] >= cfg['min_decline_from_high']]

    # 当前价格必须在反弹途中（不能还在低位趴着）
    df['near_high_ratio'] = df['last_price'] / df['highest52weeks_price']
    df = df[df['near_high_ratio'] >= cfg['min_near_high_ratio']]

    print(f"初筛候选：{len(df)} 只")
    return df.reset_index(drop=True)
