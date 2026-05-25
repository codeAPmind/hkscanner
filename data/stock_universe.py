from moomoo import SecurityType, RET_OK


def get_hk_stock_universe(ctx) -> list[str]:
    """
    港股全量正股列表，一次请求搞定。
    返回 ['HK.00700', 'HK.03277', ...]
    """
    ret, data = ctx.get_stock_basicinfo(
        market="HK",
        stock_type=SecurityType.STOCK    # 只要正股，过滤权证/ETF
    )
    if ret != RET_OK:
        raise RuntimeError(f"get_stock_basicinfo failed: {data}")

    codes = data['code'].tolist()
    print(f"港股全量正股：{len(codes)} 只")
    return codes
