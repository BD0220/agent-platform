"""冒烟测试：验证所有模块可正常导入。"""
import pytest


def test_utils_import():
    from agent_platform.utils import safe_print, clean_markdown, html_escape, safe_path
    assert callable(safe_print)
    assert callable(clean_markdown)
    assert callable(html_escape)
    assert callable(safe_path)


def test_storage_import():
    from agent_platform.storage import get_chroma_collection, get_or_create_kb_collection
    from agent_platform.storage.database import init_db, db_get_all_users, db_count_memories
    from agent_platform.storage.state import save_state, load_state, extract_task_name
    assert callable(init_db)
    assert callable(save_state)
    assert callable(load_state)
    assert callable(extract_task_name)


def test_llm_import():
    from agent_platform.llm import get_llm, on_usage, ChatResult, LLMProvider
    provider = get_llm()
    assert provider is not None
    assert hasattr(provider, "chat")
    assert callable(on_usage)


def test_tools_import():
    from agent_platform.tools import (
        ToolDefinition, register_tool, get_tool_definitions, get_tool_names,
        get_tool_schemas, execute_tool, parse_and_execute_tool_calls,
    )
    names = get_tool_names()
    assert "list_files" in names
    assert "create_file" in names
    assert "run_python_file" in names
    assert "dispatch_agent" in names
    assert "web_search" in names
    assert "fetch_url" in names
    assert "read_file" in names
    assert "search_knowledge" in names


def test_memory_import():
    from agent_platform.memory import (
        save_context, load_context, delete_context,
        search_memory, search_memories,
        extract_experience, auto_extract_and_save, classify_task, save_memory,
    )
    assert callable(save_context)
    assert callable(load_context)
    assert callable(delete_context)
    assert callable(search_memory)
    assert callable(search_memories)
    assert callable(classify_task)


def test_agents_import():
    from agent_platform.agents import (
        SYSTEM_PROMPTS, AGENT_META, call_agent, extract_code,
        dispatch_sub_agent, master_agent_loop,
        product_manager_work, programmer_work, tester_work, programmer_fix_work,
    )
    assert len(SYSTEM_PROMPTS) >= 4
    assert len(AGENT_META) == 3
    assert callable(call_agent)
    assert callable(extract_code)
    assert callable(dispatch_sub_agent)
    assert callable(master_agent_loop)


def test_rag_import():
    from agent_platform.rag import (
        BM25Index, _bm25_index, _build_bm25,
        advanced_search, format_memories_for_context,
        recursive_chunk, import_file, import_text, search_knowledge,
    )
    assert callable(advanced_search)
    assert callable(recursive_chunk)
    assert callable(import_text)
    assert callable(search_knowledge)


def test_workflow_import():
    from agent_platform.workflow import WorkflowState, build_workflow, run_workflow
    from agent_platform.workflow.nodes import (
        init_node, retrieve_node, agent_node, tool_node, conclusion_node,
        route_after_agent, route_after_tools,
    )
    assert callable(run_workflow)
    assert callable(init_node)
    assert callable(agent_node)
    assert callable(tool_node)
    assert callable(conclusion_node)
    assert callable(route_after_agent)
    assert callable(route_after_tools)

    # Verify graph compiles with correct nodes
    g = build_workflow()
    assert g is not None
    node_names = list(g.nodes.keys())
    assert "agent" in node_names
    assert "tools" in node_names
    assert "conclusion" in node_names


def test_conversation_import():
    from agent_platform.conversation import SessionManager, chat_with_agent, stream_chat_with_agent
    assert callable(chat_with_agent)


def test_auth_import():
    from agent_platform.auth import register, login, verify_token
    assert callable(register)
    assert callable(login)
    assert callable(verify_token)


def test_server_import():
    from agent_platform.server.api import app
    assert app.title == "多智能体协作平台 API"


def test_main_import():
    from agent_platform.main import run_task, main
    assert callable(run_task)
    assert callable(main)


def test_tool_count():
    from agent_platform.tools import get_tool_names
    assert len(get_tool_names()) == 8


def test_chunking():
    from agent_platform.rag.chunking import recursive_chunk
    text = "这是一段测试文本。" * 100
    chunks = recursive_chunk(text)
    assert len(chunks) >= 1
    for c in chunks:
        assert len(c) > 0


def test_session_crud():
    from agent_platform.conversation.session import get_session_manager
    mgr = get_session_manager()
    s = mgr.create("测试Agent")
    assert s.id
    assert mgr.get(s.id) is not None
    assert mgr.count >= 1
    mgr.delete(s.id)
    assert mgr.get(s.id) is None
