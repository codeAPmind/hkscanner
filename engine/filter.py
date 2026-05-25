def apply_score_filter(results: list[dict], min_score: int, max_results: int) -> list[dict]:
    """过滤低分信号，按得分+新信号排序后截断"""
    filtered = [r for r in results if r['score'] >= min_score]
    sorted_results = sorted(
        filtered,
        key=lambda x: (x['score'], int(x.get('is_new', False))),
        reverse=True
    )
    return sorted_results[:max_results]
