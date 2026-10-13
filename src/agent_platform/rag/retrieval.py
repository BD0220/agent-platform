"""经验检索 —— 单路向量检索（ChromaDB）。

2026-10-13 从简：
    之前这里是「LLM 查询扩展 → BM25 + 向量双路 → RRF 融合 → LLM 重排」的完整管线，
    是给上万条文档设计的。而向量库里躺的只是**自动沉淀的历史任务经验**，
    通常就几十条 —— 每条任务开始前为此多烧两次 LLM 调用纯属浪费。
    现在砍成单路向量检索：ChromaDB 查 top_k 条，回 MySQL 补全结构化字段，直接喂上下文。
    ChromaDB 不可用时退回关键词检索（memory/search.py），保证流程不断。
"""
from ..storage.vector_store import get_chroma_collection
from ..storage.database import db_search_memories
from ..utils.safe_print import safe_print


def _dense_search(query: str, top_k: int = 5) -> list[dict]:
    coll = get_chroma_collection()
    if not coll or coll.count() == 0:
        return []
    try:
        results = coll.query(query_texts=[query], n_results=min(top_k, coll.count()))
        memories = []
        if results and results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                doc = results["documents"][0][i] if results["documents"] else ""
                memories.append({
                    "id": doc_id,
                    "one_line_summary": doc[:100] if doc else "",
                })
        return memories
    except Exception as e:
        safe_print(f"[RAG] 向量检索失败: {e}")
        return []


def advanced_search(query: str, top_k: int = 3, **_ignored) -> list[dict]:
    """按语义相似度取 top_k 条历史经验，补全 MySQL 里的结构化字段。

    ``**_ignored`` 兼容旧调用点传的 ``use_rerank=...`` 之类参数，直接忽略。
    """
    hits = _dense_search(query, top_k=top_k)
    if not hits:
        return []

    all_memories = db_search_memories(limit=500)
    by_id = {m["id"]: m for m in all_memories}

    results = []
    for hit in hits:
        mem = by_id.get(hit["id"])
        if mem:
            results.append(mem)
        else:
            results.append({
                "id": hit["id"], "task_type": "", "tags": [],
                "task_description": "", "quality_score": 5,
                "one_line_summary": hit["one_line_summary"],
                "successes": [], "lessons": [], "improvements": [],
            })
    safe_print(f"[RAG] 命中 {len(results)} 条历史经验")
    return results


def format_memories_for_context(memories: list[dict]) -> str:
    """将检索到的经验格式化为可供 Agent 参考的上下文。"""
    if not memories:
        return ""
    lines = ["", "## 历史经验参考", "以下是从历史任务中检索出的相关经验：", ""]
    for i, mem in enumerate(memories, 1):
        lines.append(
            f"### 经验 {i}: {mem.get('one_line_summary', mem.get('task_description', '')[:80])}"
        )
        lines.append(f"- **任务类型**: {', '.join(mem.get('tags', [mem.get('task_type', '')]))}")
        lines.append(f"- **质量评分**: {mem.get('quality_score', '?')}/10")
        lines.append(f"- **原始任务**: {mem.get('task_description', '')[:150]}")
        for label, key in (("成功之处", "successes"), ("失败教训", "lessons"), ("改进建议", "improvements")):
            items = mem.get(key) or []
            if items:
                lines.append(f"- **{label}**: {'; '.join(items[:3])}")
        lines.append("")
    lines.append("**请参考上述经验，避免重复已知错误，借鉴成功做法。**")
    return "\n".join(lines)
