#!/usr/bin/env bash
# hkscanner 买卖（cron 专用）
# 工作日 14:10：避开 14:00 hk_anomaly / 14:05 YiDong anomaly-sync。
# 只读本项目 .env。不 source YiDong .env，避免把模拟盘账户带进实盘。
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

if [ -f "${PROJECT_DIR}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "${PROJECT_DIR}/.env"
  set +a
fi

if [ -z "${HKSCANNER_TRADE_ENV:-}" ] || [ -z "${HKSCANNER_FUTU_ACCOUNT_HK:-}" ]; then
  echo "[trade] hkscanner .env 缺少 HKSCANNER_TRADE_ENV 或 HKSCANNER_FUTU_ACCOUNT_HK" >&2
  exit 1
fi
if [ -z "${FUTU_TRADE_PWD_MD5:-}" ] && [ -z "${FUTU_TRADE_PWD:-}" ]; then
  echo "[trade] hkscanner .env 缺少 FUTU_TRADE_PWD_MD5" >&2
  exit 1
fi

export HK_TRADE_ENV="${HKSCANNER_TRADE_ENV}"
export FUTU_ACCOUNT_HK="${HKSCANNER_FUTU_ACCOUNT_HK}"
export FUTU_HOST="${FUTU_HOST:-127.0.0.1}"
export FUTU_PORT="${FUTU_PORT:-11112}"
export PYTHONUNBUFFERED=1
export PYTHONPATH="${YIDONG_DIR}:${PROJECT_DIR}:${PYTHONPATH:-}"
export HKSCANNER_PAPER_DB="${PROJECT_DIR}/store/paper_positions.db"
export YIDONG_ROOT="${YIDONG_DIR}"

cd "$PROJECT_DIR"
"${CONDA_BIN}" run -n "${CONDA_ENV}" python sim_trade.py "$@" >> "$LOG_FILE" 2>&1
