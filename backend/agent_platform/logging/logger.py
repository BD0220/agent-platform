"""结构化日志 & 可观测性 —— JSON 格式日志输出到文件。"""
import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime
from logging.handlers import RotatingFileHandler

from ..utils.paths import LOG_DIR

LOG_FILE = str(LOG_DIR / "platform.log")
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5


def _ensure_log_dir():
    os.makedirs(LOG_DIR, exist_ok=True)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        for attr in ["agent", "tool", "model", "task_id", "tokens", "duration_ms", "phase"]:
            val = getattr(record, attr, None)
            if val is not None:
                log_entry[attr] = val
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = {
                "type": type(record.exc_info[1]).__name__,
                "message": str(record.exc_info[1]),
                "traceback": traceback.format_exception(*record.exc_info),
            }
        return json.dumps(log_entry, ensure_ascii=False)


_loggers: dict[str, logging.Logger] = {}


def get_logger(name: str = "platform") -> logging.Logger:
    if name in _loggers:
        return _loggers[name]
    _ensure_log_dir()
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        file_handler = RotatingFileHandler(LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(JsonFormatter())
        logger.addHandler(file_handler)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"))
        logger.addHandler(console_handler)
    _loggers[name] = logger
    return logger


platform_log = get_logger("platform")


def log_llm_call(model: str, messages_count: int, prompt_tokens: int, completion_tokens: int, duration_ms: float, has_tools: bool = False):
    log = logging.getLogger("platform.llm")
    log.info(f"LLM 调用 | model={model} | msgs={messages_count} | tokens={prompt_tokens}+{completion_tokens} | time={duration_ms:.0f}ms | tools={has_tools}",
             extra={"model": model, "tokens": f"{prompt_tokens}p+{completion_tokens}c", "duration_ms": round(duration_ms, 1)})


def log_tool_call(tool_name: str, params: str = "", duration_ms: float = 0, success: bool = True, error: str = ""):
    log = logging.getLogger("platform.tool")
    extra = {"tool": tool_name, "duration_ms": round(duration_ms, 1)}
    if success:
        log.info(f"工具调用 | {tool_name} | 成功 | {duration_ms:.0f}ms", extra=extra)
    else:
        log.error(f"工具调用 | {tool_name} | 失败 | {error}", extra=extra)


def log_agent_dispatch(agent_name: str, task_len: int, task_id: str = ""):
    log = logging.getLogger("platform.agent")
    log.info(f"Agent 调度 | {agent_name} | 任务长度={task_len}", extra={"agent": agent_name, "task_id": task_id})


def log_task_start(task_id: str, user_request: str):
    platform_log.info(f"任务开始 | id={task_id} | request={user_request[:100]}", extra={"task_id": task_id})


def log_task_complete(task_id: str, turns: int, completed: bool, duration_s: float):
    platform_log.info(f"任务完成 | id={task_id} | turns={turns} | {'成功' if completed else '未完成'} | time={duration_s:.1f}s",
                      extra={"task_id": task_id, "duration_ms": round(duration_s * 1000, 1)})


def log_task_error(task_id: str, error: str):
    platform_log.error(f"任务失败 | id={task_id} | error={error[:200]}", extra={"task_id": task_id})


class Timer:
    def __init__(self, name: str = ""):
        self.name = name
        self.start_time = 0
        self.elapsed_ms = 0

    def __enter__(self):
        self.start_time = time.perf_counter()
        return self

    def __exit__(self, *args):
        self.elapsed_ms = (time.perf_counter() - self.start_time) * 1000

    @property
    def elapsed(self) -> float:
        return self.elapsed_ms / 1000


_ensure_log_dir()
platform_log.info("=" * 40)
platform_log.info("平台启动 - 日志系统初始化完成")
