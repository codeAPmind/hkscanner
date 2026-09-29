#!/usr/bin/env bash
# hkscanner 模拟买卖（cron 专用）
# 工作日 14:10：避开 14:00 hk_anomaly / 14:05 YiDong anomaly-sync。
# 异动通道 ENTRY_END_MIN=65 → 约 10:35 后不再开新仓；下午 K 线更稳。
# 券商：source YiDong .env（OpenD 11112 / SIMULATE / FUTU_ACCOUNT_HK）。
set -euo pipefail

PROJECT_DIR="/Users/openclaw/openclaw_workspace/hkscanner"
YIDONG_DIR="/Users/openclaw/openclaw_workspace/YiDong_AutoTrader_H"
RUNTIME_DIR="/Users/openclaw/openclaw_workspace/hkscanner_runtime"
LOG_FILE="${RUNTIME_DIR}/hkscanner_trade_cron.log"
CONDA_BIN="/Users/openclaw/miniconda3/bin/conda"
CONDA_ENV="auto_trader"
NOW="$(date '+%Y-%m-%d %H:%M:%S')"

mkdir -p "$RUNTIME_DIR"

{
  echo "============================================================"
  echo "[INFO] Trade task start: ${NOW}"
} >> "$LOG_FILE"

# 先扫码器 webhook，再让 YiDong 券商变量覆盖 FUTU_PORT / HK_TRADE_ENV
if [ -f "${PROJECT_DIR}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "${PROJECT_DIR}/.env"
  set +a
fi
if [ -f "${YIDONG_DIR}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "${YIDONG_DIR}/.env"
  set +a
fi

export PYTHONUNBUFFERED=1
export PYTHONPATH="${YIDONG_DIR}:${PROJECT_DIR}:${PYTHONPATH:-}"
export HKSCANNER_PAPER_DB="${PROJECT_DIR}/store/paper_positions.db"
export YIDONG_ROOT="${YIDONG_DIR}"
export HK_TRADE_ENV="${HK_TRADE_ENV:-SIMULATE}"
export FUTU_PORT="${FUTU_PORT:-11112}"

cd "$PROJECT_DIR"
"${CONDA_BIN}" run -n "${CONDA_ENV}" python sim_trade.py "$@" >> "$LOG_FILE" 2>&1
