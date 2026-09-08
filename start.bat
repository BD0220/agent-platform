@echo off
chcp 65001 >nul
REM 多智能体协作平台 一键启动 (Windows)
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo 首次运行，创建虚拟环境并安装依赖（约 1-3 分钟）...
  python -m venv .venv
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\pip.exe" install -e ".[langgraph]"
)

set PYTHONPATH=src
if not exist data mkdir data
if not exist deliveries mkdir deliveries
if not exist data\logs mkdir data\logs

echo 启动 API 服务 (端口 8000)...
start "agent-api" cmd /k "set PYTHONPATH=src && .venv\Scripts\python.exe -m uvicorn agent_platform.server.api:app --host 127.0.0.1 --port 8000"

echo 启动 Gradio UI (端口 7860)...
start "agent-ui" cmd /k "set PYTHONPATH=src && .venv\Scripts\python.exe -m agent_platform.server.ui"

echo.
echo 等待服务启动...
timeout /t 8 /nobreak >nul
echo.
echo 浏览器打开:  http://localhost:7860
echo API 文档:    http://localhost:8000/docs
echo 关闭服务: 直接关闭弹出的两个黑色窗口即可
pause
