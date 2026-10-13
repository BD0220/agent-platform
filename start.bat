@echo off
rem abundance launcher - starts the web app on http://127.0.0.1:8000
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [abundance] venv not found. Creating it now, please wait ...
    python -m venv .venv
    ".venv\Scripts\python.exe" -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -e ".[dev]"
)

".venv\Scripts\python.exe" run.py %*
