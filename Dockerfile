FROM python:3.11-slim

# 创建非 root 用户（安全最佳实践）
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

WORKDIR /app

# 系统依赖：gcc 用于编译 bcrypt 等 C 扩展；curl 用于健康检查
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖文件，利用 Docker 层缓存
COPY pyproject.toml requirements.txt ./
COPY src/ src/

# 安装核心依赖 + langgraph（工作流引擎）
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e ".[langgraph]"

# 创建运行时目录并赋权
RUN mkdir -p /app/data /app/deliveries && chown -R appuser:appuser /app

# 运行时环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AGENT_DATA_DIR=/app/data \
    AGENT_DELIVERIES_DIR=/app/deliveries

USER appuser

EXPOSE 8000 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/docs || exit 1

# 默认启动 API 服务
CMD ["uvicorn", "agent_platform.server.api:app", "--host", "0.0.0.0", "--port", "8000"]
