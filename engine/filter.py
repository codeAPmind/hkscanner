def pass_kline_filter(kfeat: dict, cfg: dict) -> bool:
    """K 线硬过滤：近 1 月、距 SMA20 过热。缺均线时过热条件不放行。"""
    r1 = kfeat.get("ret_1m")
    max_r1 = cfg.get("max_ret_1m")
    if max_r1 is not None and r1 is not None and r1 > max_r1:
        return False
    min_r1 = cfg.get("min_ret_1m")
    if min_r1 is not None and r1 is not None and r1 < min_r1:
        return False

    min_c = cfg.get("min_contraction_ratio")
    if min_c is not None:
        c = kfeat.get("contraction_pre")
        if c is None:
            c = kfeat.get("contraction_ratio", 0)
        if (c or 0) < min_c:
            return False

    min_r12 = cfg.get("min_ret_12_1")
    r12 = kfeat.get("ret_12_1")
    if min_r12 is not None and r12 is not None and r12 < min_r12:
        return False

    max_ext = cfg.get("max_ext_sma20")
    if max_ext is not None:
        ext = kfeat.get("ext_sma20")
        if ext is None or ext > max_ext:
            return False
    return True


def apply_score_filter(results: list[dict], min_score: int, max_results: int) -> list[dict]:
    """过滤低分信号，按得分+新信号排序后截断"""
    filtered = [r for r in results if r['score'] >= min_score]
    sorted_results = sorted(
        filtered,
        key=lambda x: (x['score'], int(x.get('is_new', False))),
        reverse=True
    )
    return sorted_results[:max_results]
