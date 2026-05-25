import os

# Futu OpenD
FUTU_HOST = "127.0.0.1"
FUTU_PORT = 11111

# 飞书 Webhook（汇总和详情建议分到不同群）
FEISHU_WEBHOOK_SUMMARY = os.getenv("FEISHU_WEBHOOK_SUMMARY", "")
FEISHU_WEBHOOK_DETAIL = os.getenv("FEISHU_WEBHOOK_DETAIL", "")

# 初筛条件（快照阶段）
FILTER_CONFIG = {
    "min_change_rate": 8.0,
    "max_vol_ratio": 4.0,
    "min_decline_from_high": 0.35,
    "min_near_high_ratio": 0.75,
    "min_market_cap": 5e8,
    "min_score": 60,
    "max_results": 10,
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
