#!/usr/bin/env bash
# hkscanner 买卖（cron 专用）
# 工作日 14:10：避开 14:00 hk_anomaly / 14:05 YiDong anomaly-sync。
# 券商密码/OpenD 来自 YiDong .env；仿真/实盘由 hkscanner .env 的
# HKSCANNER_TRADE_ENV 决定，避免把 YiDong 主策略一并切成 REAL。
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

# 先扫码器 webhook，再灌 YiDong 券商变量；最后用 HKSCANNER_* 盖掉环境/账户
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

if [ -n "${HKSCANNER_TRADE_ENV:-}" ]; then
  export HK_TRADE_ENV="${HKSCANNER_TRADE_ENV}"
fi
if [ -n "${HKSCANNER_FUTU_ACCOUNT_HK:-}" ]; then
  export FUTU_ACCOUNT_HK="${HKSCANNER_FUTU_ACCOUNT_HK}"
fi

export PYTHONUNBUFFERED=1
export PYTHONPATH="${YIDONG_DIR}:${PROJECT_DIR}:${PYTHONPATH:-}"
export HKSCANNER_PAPER_DB="${PROJECT_DIR}/store/paper_positions.db"
export YIDONG_ROOT="${YIDONG_DIR}"
export HK_TRADE_ENV="${HK_TRADE_ENV:-SIMULATE}"
export FUTU_PORT="${FUTU_PORT:-11112}"

cd "$PROJECT_DIR"
"${CONDA_BIN}" run -n "${CONDA_ENV}" python sim_trade.py "$@" >> "$LOG_FILE" 2>&1
