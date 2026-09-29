import asyncio
import json
import urllib.request

try:
    import httpx
except ImportError:
    httpx = None

from config import EXIT_CONFIG
from engine.exits import describe_exit_rules


async def send_feishu(webhook: str, payload: dict):
    if httpx is None:
        raise RuntimeError("httpx is required for async Feishu push")
    async with httpx.AsyncClient() as c:
        r = await c.post(webhook, json=payload, timeout=10)
        r.raise_for_status()


def build_summary_card(results: list, scan_date: str, total: int) -> dict:
    lines = []
    for r in sorted(results, key=lambda x: x['score'], reverse=True)[:10]:
        e = {"MARKUP_ACCEL": "🚀", "MARKUP": "📈", "REVERSAL": "⚡",
             "LATE_WASHOUT": "⏳"}.get(r['stage_en'], "📊")
        lines.append(
            f"{e} **{r['name']}**（{r['code'].replace('HK.', '')}）"
            f"  +{r['change']:.1f}%  量比{r['vol_ratio']}"
            f"  得分**{r['score']}**  {r['stage']}"
        )
    high_conf = sum(1 for r in results if r['score'] >= 75)
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text",
                          "content": f"🔍 港股主力形态扫描 · {scan_date}"},
                "template": "blue"
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content":
                    f"扫描 **{total}** 只 | 候选 **{len(results)}** 只 | 高确定性 **{high_conf}** 只"}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md",
                    "content": "\n".join(lines) or "今日无满足条件标的"}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md",
                    "content": "**出场** 次日开盘买，第 10 个交易日收盘卖"}},
                {"tag": "note", "elements": [{"tag": "plain_text",
                    "content": "⚠ 技术形态识别，不构成投资建议。"}]}
            ]
        }
    }


def build_detail_card(r: dict) -> dict:
    signals_md = "\n".join(f"{i} **{l}**：{d}" for i, l, d in r['signals'])
    risk_md    = "\n".join(r['risk_flags']) if r['risk_flags'] else "暂无明显风险信号"
    tpl = "green" if r['score'] >= 75 else "orange" if r['score'] >= 55 else "red"
    name = r['name']
    code = r['code'].replace('HK.', '')
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text",
                          "content": f"{r['stage']} · {name}（{code}）"},
                "template": tpl
            },
            "elements": [
                {"tag": "div", "fields": [
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**今日涨幅**\n+{r['change']:.1f}%"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**量比**\n{r['vol_ratio']}"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**换手率**\n{r['turnover']:.2f}%"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**形态得分**\n{r['score']}/100"}},
                ]},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**信号明细**\n{signals_md}"}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**风险提示**\n{risk_md}"}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md",
                    "content": f"**出场规则**\n{describe_exit_rules(EXIT_CONFIG)}"}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**AI 事件分析**\n{r.get('ai_analysis', '—')}"}},
            ]
        }
    }


async def push_results(results, scan_date, total, webhook_summary, webhook_detail):
    await send_feishu(webhook_summary, build_summary_card(results, scan_date, total))

    high_conf = [r for r in results if r['score'] >= 75]
    for r in high_conf:
        await send_feishu(webhook_detail, build_detail_card(r))
        await asyncio.sleep(12)    # 飞书同一 Webhook 限速 5条/分钟


def push_trade_card(trade_date: str, body: str, webhook: str) -> None:
    if not webhook:
        return
    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text",
                          "content": f"模拟交易 · {trade_date}"},
                "template": "turquoise",
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": body}},
                {"tag": "note", "elements": [{"tag": "plain_text",
                    "content": "Futu SIMULATE · 持 10 个交易日 · 不构成投资建议"}]},
            ],
        },
    }
    try:
        if httpx is not None:
            httpx.post(webhook, json=payload, timeout=10).raise_for_status()
            return
        req = urllib.request.Request(
            webhook,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[feishu] trade card failed: {e}")
