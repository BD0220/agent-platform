#!/usr/bin/env bash
# 多智能体协作平台 一键启动（macOS / Linux）
# 用法: ./start.sh         启动 API + UI
#       ./start.sh stop    停止
set -e
cd "$(dirname "$0")"

PY=python3
command -v python3 >/dev/null 2>&1 || PY=python
VENV=".venv"
[ -d "$VENV" ] && PY="$VENV/bin/python"

if [ "$1" = "stop" ]; then
  pkill -f "uvicorn agent_platform.server.api:app" 2>/dev/null || true
  pkill -f "agent_platform.server.ui" 2>/dev/null || true
  echo "已停止"
  exit 0
fi

# 首次运行：创建虚拟环境并安装依赖
if [ ! -d "$VENV" ]; then
  echo "首次运行，创建虚拟环境并安装依赖（约 1-3 分钟）..."
  $PY -m venv "$VENV"
  "$VENV/bin/pip" install --upgrade pip
  "$VENV/bin/pip" install -e ".[langgraph]"
fi

export PYTHONPATH=src
mkdir -p data deliveries data/logs

echo "启动 API 服务 (端口 8000)..."
nohup "$VENV/bin/python" -m uvicorn agent_platform.server.api:app --host 127.0.0.1 --port 8000 > api.log 2>&1 &
echo "API  PID=$!"

echo "启动 Gradio UI (端口 7860)..."
nohup "$VENV/bin/python" -m agent_platform.server.ui > ui.log 2>&1 &
echo "UI   PID=$!"

sleep 6
echo ""
echo "=== 状态 ==="
curl -s -o /dev/null -w "API:        http://localhost:8000/docs  -> HTTP %{http_code}\n" http://127.0.0.1:8000/docs || echo "API 启动中，查看 api.log"
curl -s -o /dev/null -w "Gradio UI:  http://localhost:7860        -> HTTP %{http_code}\n" http://127.0.0.1:7860/ || echo "UI 启动中，查看 ui.log"
echo ""
echo "浏览器打开:  http://localhost:7860"
echo "停止服务:    ./start.sh stop"
