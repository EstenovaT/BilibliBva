#!/usr/bin/env bash
# ============================================
#  B站解析服务 - 本地一键启动 (Linux/macOS)
#  用法: bash start_local.sh
# ============================================
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
    echo "[ERROR] python3 not found. Please install Python 3.8+."
    exit 1
fi
exec python3 "$(dirname "$0")/start_local.py"
