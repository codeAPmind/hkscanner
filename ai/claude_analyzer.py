import asyncio
import httpx

from config import DEEPSEEK_CONFIG, AI_CONFIG

API_KEY = DEEPSEEK_CONFIG["api_key"]
BASE_URL = DEEPSEEK_CONFIG["base_url"].rstrip("/")
MODEL = DEEPSEEK_CONFIG["model"]

PROMPT = """\
你是专注港股主力资金的分析师，擅长识别主力操盘节点与事件驱动逻辑。

## 股票数据
- 标的：{name}（{code}.HK）  行业：{industry}
- 今日涨幅：+{change:.1f}%  量比：{vol_ratio}  换手率：{turnover:.2f}%
- 52周区间：{low52w} → {high52w}，当前 {price}（位于区间 {near_high:.0%}）
- 底部缩量程度：{contraction:.0%} 时间处于缩量状态
- 形态得分：{score}/100  阶段：{stage}

## 分析要求（每条 ≤ 40 字，直接输出结论）

**1. 形态确认**
量比 {vol_ratio} + 今日 +{change:.1f}% 说明什么？主力意图判断？

**2. 事件驱动（最重要）**
结合 {industry} 行业，近期哪些政策/行业事件/市场主题最可能驱动这次异动？
给出 2~3 个方向（无需确认是否真实发生，分析逻辑方向即可）。

**3. 操作节奏**
确定性：高/中/低。出场按系统规则：次日开盘买，第 10 个交易日收盘卖。不要另给止盈止损价。

格式：直接按三个加粗标题输出，不要废话。
"""


def analyze_stock(info: dict) -> str:
    prompt = PROMPT.format(**info)
    resp = httpx.post(
        f"{BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": AI_CONFIG["max_tokens"],
        },
        timeout=60.0,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


async def analyze_batch(candidates: list[dict], concurrency: int = 3) -> list[dict]:
    sem  = asyncio.Semaphore(concurrency)
    loop = asyncio.get_event_loop()

    async def _run(r):
        async with sem:
            r['ai_analysis'] = await loop.run_in_executor(None, analyze_stock, r)
            await asyncio.sleep(0.5)
            return r

    return await asyncio.gather(*[_run(r) for r in candidates])
