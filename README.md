# hkscanner

港股扫描器：通过 Futu OpenD 拉取行情，初筛 + K 线形态打分，可选 DeepSeek 分析，飞书推送结果。工作日 14:10 用 YiDong 模拟账户按「次日买、持 10 日」下单。

## 环境

- Python 3.10+
- [Futu OpenD](https://www.futunn.com/download/openAPI) 本地运行
- 复制 `config.example.py` 为 `config.py` 并填写密钥
- 买卖不写券商密码：`hkscanner_trade_run.sh` 会 source `YiDong_AutoTrader_H/.env`（`FUTU_PORT=11112`、`HK_TRADE_ENV=SIMULATE`）

## 安装

```bash
pip install -r requirements.txt
```

## 运行

```bash
python run_scan.py
```

模拟买卖（auto_trader 环境，复用 YiDong 执行器）：

```bash
/Users/openclaw/openclaw_workspace/hkscanner_trade_run.sh --dry-run
/Users/openclaw/openclaw_workspace/hkscanner_trade_run.sh
```

## Cron

- 工作日 **18:20** 扫描 + 飞书（`hkscanner_run.sh`）
- 工作日 **14:10** 模拟卖到期仓、买昨日信号（`hkscanner_trade_run.sh`）
