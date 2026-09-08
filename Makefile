.PHONY: install dev test lint format docker-up docker-down clean help

PYTHON ?= python
PIP ?= pip

help: ## 显示所有可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## 安装生产依赖
	$(PIP) install -e ".[langgraph]"

dev: ## 安装开发依赖（含测试和代码检查）
	$(PIP) install -e ".[dev,langgraph]"

test: ## 运行所有测试（并发/沙箱/数据库/导入）
	$(PYTHON) -m pytest tests/ -v --tb=short

test-quick: ## 快速测试（跳过耗时的超时测试）
	RUN_SLOW_TESTS=0 $(PYTHON) -m pytest tests/ -v --tb=short -k "not timeout"

test-concurrency: ## 仅运行并发隔离测试
	$(PYTHON) tests/test_concurrency.py

test-sandbox: ## 仅运行沙箱安全测试
	$(PYTHON) tests/test_sandbox.py

lint: ## 代码检查（ruff）
	ruff check src/ tests/

format: ## 自动格式化（black + ruff --fix）
	black src/ tests/
	ruff check --fix src/ tests/

run-api: ## 启动 FastAPI 服务
	uvicorn agent_platform.server.api:app --host 0.0.0.0 --port 8000 --reload

run-ui: ## 启动 Gradio UI
	$(PYTHON) -m agent_platform.server.ui

run-cli: ## 命令行模式运行
	$(PYTHON) -m agent_platform

docker-up: ## Docker Compose 启动全部服务
	docker compose up -d --build

docker-down: ## 停止 Docker 服务
	docker compose down

docker-logs: ## 查看 Docker 日志
	docker compose logs -f api

clean: ## 清理运行时产物和缓存
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf build/ dist/ *.egg-info src/*.egg-info
	rm -rf data/chroma_db/ data/logs/ data/platform.db data/memory.json data/tokens.json data/state_*.json data/.session_*.json
	rm -rf deliveries/
