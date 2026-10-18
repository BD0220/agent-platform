"""
主调度器 —— 多智能体协作平台入口。
主Agent 作为总调度，通过 dispatch_agent 工具调度产品经理、程序员、测试员。
使用 LangGraph 编排工作流，未安装时回退到线性 master_agent_loop。
"""
from .storage.state import load_state, get_state_summary
from .utils.safe_print import safe_print


def run_task(user_request: str) -> dict:
    """
    执行完整的多智能体协作流程，返回最终状态字典。
    通过 LangGraph 工作流引擎编排，未安装 langgraph 时回退到线性模式。
    """
    from .workflow.graph import run_workflow
    return run_workflow(user_request)


def main():
    safe_print("=" * 60)
    safe_print("       多智能体协作平台")
    safe_print("   产品经理 | 程序员 | 测试员")
    safe_print("=" * 60)
    safe_print()

    user_request = input("请输入您的需求：").strip()
    if not user_request:
        safe_print("需求不能为空，程序退出。")
        return

    safe_print(f"\n>>> 收到需求：{user_request}")

    result = run_task(user_request)

    safe_print("\n" + "=" * 60)
    safe_print("       最终状态摘要")
    safe_print("=" * 60)
    for key, value in result.items():
        safe_print(f"\n{'─' * 40}")
        safe_print(f"【{key}】")
        text = value if isinstance(value, str) else str(value)
        safe_print(text[:200] + ("..." if len(text) > 200 else ""))

    safe_print("\n程序结束。")


if __name__ == "__main__":
    main()
