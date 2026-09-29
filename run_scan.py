import asyncio
from datetime import datetime
from config import FUTU_HOST, FUTU_PORT, FILTER_CONFIG, AI_CONFIG
from config import FEISHU_WEBHOOK_SUMMARY, FEISHU_WEBHOOK_DETAIL
from data.futu_client      import FutuClient
from data.stock_universe   import get_hk_stock_universe
from data.snapshot_fetcher import fetch_snapshots, initial_filter
from data.kline_fetcher    import fetch_klines_for_candidates
from data.kline_cache      import KlineCache
from engine.preprocess     import calc_kline_features
from engine.pattern_scorer import score_pattern
from ai.claude_analyzer    import analyze_batch
from notify.feishu         import push_results
from store.signal_db       import SignalDB


async def run_full_scan():
    scan_date = datetime.today().strftime("%Y-%m-%d")
    print(f"\n{'=' * 50}\n扫描开始：{scan_date} {datetime.now().strftime('%H:%M:%S')}")

    cache = KlineCache()
    db    = SignalDB()

    with FutuClient(FUTU_HOST, FUTU_PORT) as fc:
        ctx = fc.ctx

        # Step 1: 全量列表
        all_codes = get_hk_stock_universe(ctx)

        # Step 2: 快照批量初筛
        snap_df = fetch_snapshots(ctx, all_codes)
        cand_df = initial_filter(snap_df, FILTER_CONFIG)

        if cand_df.empty:
            print("初筛无候选股，今日不推送")
            return

        candidate_codes = cand_df['code'].tolist()

        # Step 3: 历史K线精筛（只对候选股）
        kline_map = fetch_klines_for_candidates(ctx, candidate_codes, cache)

    # 合并打分
    results = []
    for _, snap_row in cand_df.iterrows():
        code  = snap_row['code']
        kfeat = calc_kline_features(kline_map.get(code))
        snap  = snap_row.to_dict()
        pat   = score_pattern(snap, kfeat)

        if pat['score'] < FILTER_CONFIG['min_score']:
            continue

        # 10 日持仓假设：冷却期内同一标的不再推，避免把一波行情算成多笔
        if not db.is_new_signal(code, FILTER_CONFIG['signal_cooldown_days']):
            continue

        r = {
            "code":        code,
            "name":        snap.get('name', code),
            "change":      snap.get('change_rate', 0),
            "vol_ratio":   snap.get('volume_ratio', 0),
            "turnover":    snap.get('turnover_rate', 0),
            "price":       snap.get('last_price', 0),
            "high52w":     snap.get('highest52weeks_price', 0),
            "low52w":      snap.get('lowest52weeks_price', 0),
            "low_today":   snap.get('low_price', 0),
            "industry":    snap.get('stock_owner', '未知'),
            "near_high":   snap.get('last_price', 0) / max(snap.get('highest52weeks_price', 1), 1),
            "contraction": kfeat.get('contraction_ratio', 0),
            "is_new":      True,
            **pat,
        }
        results.append(r)

    # 排序：得分 > 新信号优先
    results = sorted(results, key=lambda x: (x['score'], int(x['is_new'])), reverse=True)
    results = results[:FILTER_CONFIG['max_results']]
    print(f"最终候选：{len(results)} 只")

    if not results:
        print("无高质量信号，今日不推送")
        return

    # Step 4: AI 分析
    if AI_CONFIG['enable_ai']:
        results = await analyze_batch(results, concurrency=AI_CONFIG['concurrency'])

    # Step 5: 飞书推送
    await push_results(results, scan_date, len(all_codes),
                       FEISHU_WEBHOOK_SUMMARY, FEISHU_WEBHOOK_DETAIL)

    # Step 6: 持久化
    db.save(results, scan_date)
    cache.cleanup()

    print(f"扫描完成 {datetime.now().strftime('%H:%M:%S')} | 推送 {len(results)} 只")


if __name__ == "__main__":
    asyncio.run(run_full_scan())
