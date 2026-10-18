@echo off
rem abundance launcher - starts the web app on http://127.0.0.1:8000
rem NOTE: keep this file pure ASCII. cmd.exe parses .bat with the system ANSI
rem code page (GBK on a Chinese Windows) BEFORE chcp takes effect, and UTF-8
rem Chinese comments get decoded into stray quotes/backslashes that shred the
rem command lines (and the error message is mojibake, so it looks unexplainable).
cd /d "%~dp0"

if not exist ".env" (
    if exist ".env.example" (
        echo [abundance] .env not found, copying .env.example ...
        copy /y ".env.example" ".env" >nul
    )
)

if not exist ".venv\Scripts\python.exe" (
    echo [abundance] venv not found. Creating it now, please wait ...
    if defined ABUNDANCE_PYTHON (
        "%ABUNDANCE_PYTHON%" -m venv .venv
    ) else (
        python -m venv .venv
    )
    if not exist ".venv\Scripts\python.exe" (
        echo [abundance] failed to create venv. Install Python 3.12 and retry,
        echo             or set ABUNDANCE_PYTHON to a python.exe path.
        exit /b 1
    )
    rem ".[dev,langgraph]" matters: with langgraph missing the workflow engine
    rem silently falls back to the linear loop without reporting any error.
    ".venv\Scripts\python.exe" -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -e ".[dev,langgraph]"
    if errorlevel 1 (
        echo [abundance] dependency install failed, retrying with default index ...
        ".venv\Scripts\python.exe" -m pip install -e ".[dev,langgraph]"
    )
)

".venv\Scripts\python.exe" run.py %*
