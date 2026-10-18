"""多智能体协作平台 —— 产品经理 | 程序员 | 测试员，主Agent 调度。"""

# 任何入口（uvicorn / pytest / 容器）只要 import 本包，就先把 .env 读进来。
from .utils.env import load_env as _load_env

_load_env()
