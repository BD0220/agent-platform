# abundance · 多智能体协作平台

> 一句话：发一句需求，产品经理 → 程序员 → 测试员三个 Agent 接力干活，
> 主 Agent 调度并审查，**每一步交接前先问你**，全程在网页上实时可见。

技术栈：**FastAPI + 原生 HTML/JS 前端（SSE 实时推送） + LangGraph（ReAct） + MySQL（SQLAlchemy） + ChromaDB（经验向量库）**，纯 Python 后端，单端口 `127.0.0.1:8000`。

## 快速开始

```bat
:: Windows：双击 start.bat（首次自动建 venv 装依赖），浏览器自动打开 http://127.0.0.1:8000
:: 或者手动：
.venv\Scripts\python.exe run.py
```

`.env` 需要两块配置（都在项目根目录，已被 gitignore）：

```ini
# LLM（用哪家的填哪家的 key）
DEEPSEEK_API_KEY=sk-xxx
# MySQL（没配则自动兜底 SQLite，正式使用建议配）
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=abundance
MYSQL_PASSWORD=xxx
MYSQL_DATABASE=abundance
```

> DeepSeek 的 API 只认 `deepseek-flash` / `deepseek-v4-pro` 两个模型名（2026-10 实测）。

## 它是怎么工作的

```
浏览器（frontend/，原生 JS + EventSource）
   │  SSE 实时收事件流
   ▼
FastAPI :8000 ── api.py（任务/知识库/统计）
                ├─ session_api.py   会话：新建/事件流/交接审批/终止
                ├─ session_runner.py 每条会话一个后台线程
                ├─ model_api.py     多模型信息（界面已从简移除，接口保留）
                └─ file_api.py      交付物文件读取（工作空间白名单）
                        │
        ┌───────────────▼────────────────┐
        │  LangGraph ReAct 工作流          │
        │  init → retrieve → agent ⇄ tools│
        │  → review（独立审查员）          │
        │  主Agent 通过 dispatch_agent 调度 │
        │  产品经理 / 程序员 / 测试员        │
        └───────────────┬────────────────┘
                        │
     事件总线 SessionBus（SSE 推流 + JSONL 落盘）
     控制面 ControlPlane（交接闸门：threading.Event 挂起等人工确认）
                        │
     MySQL(tasks/memories) · ChromaDB(经验向量) · 交付目录
```

几个值得看的设计：

- **交接闸门**：`tools/builtins/dispatch.py` 是全项目唯一的任务交接点，人工确认就插在这一处；`threading.Event` 把 Agent 线程挂起，点「确认 / 打回 / 终止」才放行。
- **实时输出**：子 Agent 的 LLM 调用是流式的（`chat_collect`），生成内容按 0.3s 节流推给浏览器——谁在干活，谁的气泡就在逐字长字。增量只进内存不落盘，历史回放用最终产出。
- **协作式取消**：终止不是杀线程，而是在检查点抛 `TaskStopped` 沿调用栈退出，状态与交付物保持完整。
- **质量闭环（本轮升级核心）**：子 Agent 输出统一"信封"（status/artifacts/evidence/confidence）并接受机械校验，空壳输出自动打回；任务收尾强制过**独立审查员**——它只看交付物与证据、隔离对话历史，reject 走打回通道（上限 2 次防死循环）。
- **上下文预算**：消息按预算分级拼装，超限压缩早期轮次、工具证据不压；轮数硬截断升级为"轮数 + 预算"双闸，长任务不丢早期需求。
- **两级人工闸门**：开场先确认主 Agent 的**执行计划**（看一次全貌），后续交接自动放行、进度照常上屏；设 `AGENT_GATE_MODE=all` 恢复逐次确认。
- **评测与可观测**：`scripts/eval/` 8 用例 × 4 维度跑批评测，失败自动归因（模型波动 / 轮数耗尽 / 工具失败），两轮跑批自动对比回归；每次 LLM 调用记 token 与耗时，`GET /sessions/{id}/trace` 返回决策时间线。
- **经验沉淀**：任务完成后自动提炼经验存入 MySQL + ChromaDB，下次相似任务开始前向量检索 top_k 注入上下文（2026-10 从简：单路向量检索，砍掉了 BM25/RRF/LLM 重排——几十条数据用不上那套管线）。

## 目录结构

```
agent-platform/
├── run.py / start.bat        # 启动入口（单端口 8000，就绪后自动开浏览器）
├── backend/agent_platform/   # 后端（Python 包，import 名仍是 agent_platform）
│   ├── server/               # api.py + session_api + model_api + session_runner
│   ├── runtime/              # bus.py 事件总线 / control.py 闸门 / session_store.py / tool_events.py / workspace.py
│   ├── agents/  workflow/  tools/   # Agent 内核（LangGraph 编排、工具注册、沙箱）
│   ├── llm/                  # 多 Provider 工厂 + 模型注册表
│   ├── rag/  memory/         # 经验检索与沉淀
│   └── storage/              # database.py（MySQL/SQLAlchemy）+ state.py
├── frontend/                 # 前端：index.html + css + js（md / chat / panels / app）
├── docs/                     # 改动记录.md（真实 bug 复盘）、升级清单.md、本地运行说明.md、界面截图
├── scripts/                  # 一次性脚本与评测：seed_knowledge.py、migrate_sqlite_to_mysql.py、eval/（8 用例跑批 + 归因）
├── tests/                    # pytest：98 个用例（并发/沙箱/数据库/协作/契约/审查/渲染/服务冒烟）
└── .data/                    # 运行时产物 + 缓存（会话/日志/向量库/交付物），已 gitignore
```

## API 速览（`/docs` 有交互式文档）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /sessions/start | 新建会话并开始执行任务 |
| GET | /sessions/{id}/events | SSE 事件流（游标增量） |
| POST | /sessions/{id}/approval | 交接审批：approve / reject / stop |
| POST | /sessions/{id}/stop | 协作式终止 |
| GET | /sessions/{id}/trace | 决策时间线（token 计量 / 每步事件） |
| GET | /files/content | 读交付物（限工作空间内，越权 403） |
| GET | /stats · /metrics | 任务统计 / token 用量 |
| POST | /run · /task | 旧版单发任务接口（保留兼容） |

## 测试

```bash
.venv\Scripts\python.exe -m pytest tests -q    # 98 个用例
```
