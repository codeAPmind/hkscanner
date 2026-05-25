# hkscanner

港股扫描器：通过 Futu OpenD 拉取行情，初筛 + K 线形态打分，可选 DeepSeek 分析，飞书推送结果。

## 环境

- Python 3.10+
- [Futu OpenD](https://www.futunn.com/download/openAPI) 本地运行
- 复制 `config.example.py` 为 `config.py` 并填写密钥

## 安装

```bash
pip install -r requirements.txt
```

## 运行

```bash
python run_scan.py
```

定时任务见 `scheduler.py`。
