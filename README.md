# 多智能体协作平台 (Agent Platform)

> 基于 LangGraph + Function Calling 的多智能体协作系统，主 Agent 通过 ReAct 循环动态调度产品经理、程序员、测试员三个子 Agent，协同完成从需求分析到代码交付的完整开发流程。

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2+-green.svg)](https://github.com/langchain-ai/langgraph)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg)](https://fastapi.tiangolo.com)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/BD0220/agent-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/BD0220/agent-platform/actions)

## 🎮 在线体验

👉 **[点击打开在线 Demo](https://bd0220.github.io/agent-platform/)**（无需安装，浏览器或手机直接体验）

> Demo 为纯前端交互演示版本，完整还原了深色主题 UI、Agent 协作流程与任务交付动效。完整版需配置 DeepSeek API Key 后本地运行，可真实驱动 LLM 完成代码开发任务。

## ✨ 核心特性

- **🧠 ReAct 主 Agent 调度**：基于 LangGraph 构建 `init → retrieve → agent ↔ tools → conclusion` 状态机，主 Agent 通过 Function Calling 自主决策调度子 Agent、审查产出、退回重做
- **👥 三角色协作**：产品经理（需求分析）、程序员（代码生成）、测试员（自动测试），主 Agent 动态编排而非固定流水线
- **🔀 并发任务隔离**：使用 `contextvars.ContextVar` 按 task_id 隔离状态，Redis key 按任务命名空间隔离，支持线程池多任务并行执行
- **🔒 代码执行沙箱**：通过 `resource.setrlimit` 限制 CPU/内存/文件大小/进程数，清除子进程敏感环境变量，支持 Docker 一次性容器强隔离（禁网 + 只读根文件系统）
- **📚 混合 RAG 检索**：BM25 中文 2/3-gram 稀疏检索 + ChromaDB 稠密向量检索 + RRF 融合排序 + LLM 重排，从历史经验中检索相似任务
- **🧠 分层记忆**：短期记忆（Redis + JSON 降级）+ 长期记忆（ChromaDB 向量库 + SQLite 结构化存储），任务完成后自动提取经验沉淀
- **🔌 统一工具注册**：每个工具同时提供执行函数、人读描述、Function Calling JSON Schema，支持标准 FC 和文本格式双协议
- **🌐 多 LLM Provider**：抽象 LLMProvider 基类，支持 DeepSeek / OpenAI / Claude，一行配置切换
- **📡 异步任务队列 + SSE 流式**：ThreadPoolExecutor 后台执行，Server-Sent Events 实时推送阶段进度
- **🛡️ 工程化**：bcrypt 认证、SQLite WAL 模式、结构化 JSON 日志、Docker Compose 一键部署、GitHub Actions CI

## 架构概览

```
┌─────────────────────────────────────────────────────┐
│                    API / Gradio UI                   │
│            FastAPI (/run, /task, /chat)             │
└──────────────────────┬──────────────────────────────┘
                       │
              ┌────────▼────────┐
              │   Task Queue     │  ThreadPoolExecutor
              │  (contextvars)   │  按 task_id 隔离
              └────────┬────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│              LangGraph ReAct Workflow                │
│  init → retrieve → agent ↔ tools → conclusion      │
│                          │                          │
│         ┌────────────────┼────────────────┐         │
│         ▼                ▼                ▼         │
│   ┌──────────┐    ┌──────────┐    ┌──────────┐     │
│   │ 产品经理  │    │  程序员   │    │  测试员   │     │
│   └──────────┘    └────┬─────┘    └──────────┘     │
│                        │ 沙箱执行                    │
│                 ┌──────▼──────┐                     │
│                 │ Python沙箱   │ rlimit + Docker     │
│                 └─────────────┘                     │
└─────────────────────────────────────────────────────┘
         │              │              │
    ┌────▼────┐   ┌─────▼─────┐  ┌────▼────┐
    │  Redis   │   │ ChromaDB  │  │ SQLite  │
    │ 短期记忆  │   │ 长期向量   │  │ 任务/用户│
    └─────────┘   └───────────┘  └─────────┘
```

## 快速开始

### 环境要求

- Python 3.11+
- Redis（可选，无 Redis 时自动降级为 JSON 文件）
- Docker（可选，用于沙箱强隔离和一键部署）

### 本地安装

```bash
# 克隆仓库
git clone https://github.com/BD0220/agent-platform.git
cd agent-platform

# 安装依赖（含 LangGraph 工作流引擎）
pip install -e ".[dev,langgraph]"

# 配置环境变量
cp .env.example .env
# 编辑 .env，填入你的 DEEPSEEK_API_KEY
```

### 启动服务

```bash
# API 服务
make run-api
# 或: uvicorn agent_platform.server.api:app --host 0.0.0.0 --port 8000

# Gradio UI（另一个终端）
make run-ui

# 命令行模式
make run-cli
```

API 启动后访问 `http://localhost:8000/docs` 查看交互式文档。

### Docker 一键部署

```bash
# 先在 .env 中配置好 API Key
docker compose up -d --build
```

服务启动后：
- API: http://localhost:8000
- Gradio UI: http://localhost:7860
- Redis: localhost:6379

## 项目结构

```
agent-platform/
├── src/agent_platform/
│   ├── agents/           # Agent 定义（主 Agent + 3 个子 Agent）
│   │   ├── definitions.py    # Agent 元数据、System Prompt、步骤映射
│   │   ├── master.py         # 主 Agent 调度循环
│   │   ├── sub_agents.py     # 产品经理/程序员/测试员实现
│   │   ├── call_agent.py     # LLM 调用封装
│   │   └── extractor.py      # 代码提取工具
│   ├── workflow/         # LangGraph 工作流
│   │   ├── graph.py          # 状态图构建、ReAct 循环
│   │   ├── nodes.py          # init/retrieve/agent/tool/conclusion 节点
│   │   └── state.py          # WorkflowState TypedDict
│   ├── tools/            # 统一工具系统
│   │   ├── registry.py       # 工具注册表（函数+描述+FC Schema）
│   │   ├── execution.py      # 工具执行器
│   │   ├── parser.py         # 文本格式工具调用解析
│   │   └── builtins/         # 内置工具
│   │       ├── python_tools.py  # 代码执行沙箱（rlimit + Docker）
│   │       ├── file_tools.py    # 文件读写（路径穿越防护）
│   │       ├── web_tools.py     # 网页搜索/抓取
│   │       └── dispatch.py      # 子 Agent 调度工具
│   ├── llm/              # 多 Provider LLM 抽象层
│   │   ├── base.py           # LLMProvider 抽象基类
│   │   ├── factory.py        # Provider 工厂
│   │   ├── _openai_compatible.py  # OpenAI 兼容基类
│   │   ├── deepseek.py       # DeepSeek
│   │   ├── openai.py         # OpenAI
│   │   └── claude.py         # Anthropic Claude
│   ├── rag/              # 混合检索增强生成
│   │   ├── bm25.py           # BM25 中文 n-gram 检索
│   │   ├── chunking.py       # 递归分块
│   │   ├── retrieval.py      # 混合检索 + RRF 融合 + 重排
│   │   ├── vector_store.py   # ChromaDB 封装
│   │   └── import_docs.py    # 知识库导入
│   ├── memory/           # 分层记忆系统
│   │   ├── short_term.py     # Redis 短期记忆
│   │   ├── long_term.py      # ChromaDB 长期记忆
│   │   ├── extraction.py     # 经验自动提取
│   │   └── search.py         # 记忆搜索
│   ├── storage/         # 持久化层
│   │   ├── state.py          # ContextVar 并发状态管理
│   │   ├── database.py       # SQLite（users/tasks/memories）
│   │   └── vector_store.py   # ChromaDB 单例
│   ├── queue/            # 异步任务队列
│   │   └── task_queue.py     # ThreadPoolExecutor + 生命周期
│   ├── server/           # HTTP 服务
│   │   ├── api.py            # FastAPI 路由（认证/任务/对话/知识库）
│   │   └── ui.py             # Gradio 界面
│   ├── auth/             # 认证
│   │   └── auth.py           # bcrypt + Token 会话
│   ├── mcp/              # MCP 协议桥接
│   │   └── bridge.py         # JSON-RPC over stdio
│   ├── conversation/     # 对话式交互
│   ├── logging/          # 结构化 JSON 日志
│   └── utils/            # 工具函数（路径/文本/安全打印）
├── tests/                # 测试（并发/沙箱/数据库/导入）
├── .github/workflows/    # GitHub Actions CI
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
└── Makefile
```

## API 快速参考

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/register` | 用户注册 |
| POST | `/login` | 用户登录，返回 Bearer Token |
| POST | `/run` | 同步执行任务 |
| GET | `/run/stream` | SSE 流式执行，实时推送进度 |
| POST | `/task` | 提交异步任务（线程池） |
| GET | `/task/{id}/status` | 查询任务状态和进度 |
| GET | `/task/{id}/result` | 获取任务结果 |
| DELETE | `/task/{id}` | 取消任务 |
| GET | `/tasks` | 任务列表 |
| POST | `/chat/session` | 创建对话会话 |
| POST | `/chat/session/{id}/message` | 发送消息 |
| GET | `/stats` | 平台统计 |
| GET | `/metrics` | Token 用量指标 |

## 技术亮点与设计决策

### 1. ContextVar 并发隔离

TaskManager 使用 ThreadPoolExecutor 并发执行任务。最初使用全局变量 `_current_task_id`，导致多线程下状态串台。重构为 `contextvars.ContextVar`：

```python
_current_task_id = contextvars.ContextVar("current_task_id", default=None)
```

每个工作线程独立持有 task_id，Redis key 按 `task:{task_id}:state` 隔离，交付目录追加 task_id 后缀。`contextvars.copy_context()` 确保 ThreadPoolExecutor 正确传播上下文。

### 2. 代码执行沙箱

Agent 生成的代码需要实际运行验证，安全风险高。采用多层防御：

- **路径限制**：`safe_path()` 用 `os.path.basename` + `normpath` 防止路径穿越
- **资源限制**：`preexec_fn` 中设置 `RLIMIT_CPU/AS/FSIZE/NPROC/CORE`
- **环境清理**：子进程 env 移除所有 `API_KEY/SECRET/TOKEN` 前缀变量
- **超时终止**：`subprocess.run(timeout=30)`
- **Docker 强隔离**：`--network none --readonly --memory 512m --pids-limit 32`

### 3. 混合 RAG 检索

单一检索方式各有短板：BM25 擅长精确关键词匹配但无语义理解，向量检索擅长语义但对专有名词和代码标识符不敏感。系统融合两者：

1. 查询扩展（LLM 生成同义词）
2. BM25 中文 2/3-gram 检索
3. ChromaDB cosine 向量检索
4. RRF（Reciprocal Rank Fusion）融合排序
5. LLM 对 Top-K 结果重排

### 4. 双层存储降级

短期记忆优先写 Redis，连接失败自动降级为 JSON 文件；数据库路径、数据目录全部通过环境变量可配置。Docker 中用 Volume 持久化，本地开发零依赖即可运行。

## 测试

```bash
# 全部测试
make test

# 仅并发隔离测试
make test-concurrency

# 仅沙箱安全测试
make test-sandbox
```

测试覆盖：
- **并发隔离**：4 线程同时读写不同任务状态，验证零串台
- **沙箱安全**：正常执行、环境变量隔离、错误码、超时终止
- **数据库 CRUD**：用户/任务/记忆的增删改查 + 并发创建
- **模块导入**：全部 15 个模块的冒烟测试

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LLM_PROVIDER` | `deepseek` | LLM 提供商：deepseek/openai/claude |
| `DEEPSEEK_API_KEY` | - | DeepSeek API Key |
| `OPENAI_API_KEY` | - | OpenAI API Key |
| `ANTHROPIC_API_KEY` | - | Anthropic API Key |
| `REDIS_HOST` | `localhost` | Redis 地址 |
| `REDIS_PORT` | `6379` | Redis 端口 |
| `AGENT_DATA_DIR` | `./data` | 数据目录 |
| `AGENT_DELIVERIES_DIR` | `./deliveries` | 交付物目录 |
| `AGENT_DOCKER_SANDBOX` | `0` | 设为 `1` 启用 Docker 沙箱 |

## License

MIT
