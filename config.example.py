import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Futu OpenD
FUTU_HOST = "127.0.0.1"
FUTU_PORT = 11111

# 飞书 Webhook（汇总和详情建议分到不同群）
FEISHU_WEBHOOK_SUMMARY = os.getenv("FEISHU_WEBHOOK_SUMMARY", "")
FEISHU_WEBHOOK_DETAIL = os.getenv("FEISHU_WEBHOOK_DETAIL", "")

# 初筛：量比[2,4]、市值[5亿,50亿)。
# 过热不买：收盘/次日开盘相对昨收 SMA20 不超过 +40%。
FILTER_CONFIG = {
    "min_change_rate": 8.0,
    "min_vol_ratio": 2.0,
    "max_vol_ratio": 4.0,
    "min_decline_from_high": 0.35,
    "min_near_high_ratio": 0.75,
    "min_market_cap": 5e8,
    "max_market_cap": 50e8,
    "min_score": 60,
    "max_results": 3,
    # 最多持 20 个交易日，日历 28 天覆盖周末
    "signal_cooldown_days": 28,
    # K 线硬过滤（快照初筛之后）。近 1 月=截至昨收的 21 日收益。
    "min_ret_1m": 0.08,
    "max_ret_1m": None,
    "min_contraction_ratio": None,
    "min_ret_12_1": None,
    "max_ext_sma20": 0.40,
}

# 出场：现价回到 20 日均线（昨收 SMA，布林中轨）；最多拿 20 个交易日。
EXIT_CONFIG = {
    "exit_mode": "bb_mid",
    "bb_period": 20,
    "hard_stop_pct": None,
    "hold_max_days": 20,
    "ratchet_tiers": [],
}

DEEPSEEK_CONFIG = {
    "api_key": os.getenv("DEEPSEEK_API_KEY", ""),
    "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
    "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
}

AI_CONFIG = {
    "enable_ai": True,
    "model": DEEPSEEK_CONFIG["model"],
    "max_tokens": 600,
    "concurrency": 3,
}

# 模拟交易：工作日 14:10，复用 YiDong OpenD 11112 + SIMULATE 账户。
# 券商密码/账户不要写在这里，由 hkscanner_trade_run.sh source YiDong .env。
def _env_int(key: str, default: int) -> int:
    v = os.getenv(key, "")
    return int(v) if v.strip() else default


def _env_float(key: str, default: float) -> float:
    v = os.getenv(key, "")
    return float(v) if v.strip() else default


TRADE_CONFIG = {
    "channel": "HKSCANNER",
    "max_positions": _env_int("HKSCANNER_MAX_POSITIONS", 3),
    "slot_hkd": _env_float("HKSCANNER_MAX_POSITION_HKD", 15000),
    "hold_max_days": EXIT_CONFIG["hold_max_days"],
    "signal_stale_days": 4,
    "fill_wait_sec": 45,
}
