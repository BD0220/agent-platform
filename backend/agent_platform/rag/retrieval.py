"""经验检索 —— 单路向量检索（ChromaDB）。

2026-10-13 从简：
    之前这里是「LLM 查询扩展 → BM25 + 向量双路 → RRF 融合 → LLM 重排」的完整管线，
    是给上万条文档设计的。而向量库里躺的只是**自动沉淀的历史任务经验**，
    通常就几十条 —— 每条任务开始前为此多烧两次 LLM 调用纯属浪费。
    现在砍成单路向量检索：ChromaDB 查 top_k 条，回 MySQL 补全结构化字段，直接喂上下文。
    ChromaDB 不可用时退回关键词检索（memory/search.py），保证流程不断。

2026-10-19 补「记忆治理」：
    原实现把 similarity 整段丢掉、top_k 恒返回，等于"无论多不相关都塞 3 条进
    prompt"。现在把分数带回来，并按 ``AGENT_MEMORY_MIN_SCORE`` 过滤（默认 0 =
    只观测不拦截，先把真实分布跑出来再定阈值）。
"""
import os

from ..storage.database import db_search_memories
from ..storage.vector_store import get_chroma_collection
from ..utils.safe_print import safe_print

# 相似度下限：低于它的经验不进上下文。默认 0 表示不过滤（只记录），
# 因为 Chroma 的 cosine 距离尺度要按真实数据标定后再定阈值，拍脑袋设会误杀。
MIN_SCORE = float(os.environ.get("AGENT_MEMORY_MIN_SCORE", "0"))

# 低质量经验即使相似度高也不该影响决策（质量分来自任务复盘，1-10）
MIN_QUALITY = int(os.environ.get("AGENT_MIN_MEMORY_QUALITY", "5"))


def _dense_search(query: str, top_k: int = 5) -> list[dict]:
    coll = get_chroma_collection()
    if not coll or coll.count() == 0:
        return []
    try:
        results = coll.query(query_texts=[query], n_results=min(top_k, coll.count()))
        memories = []
        if results and results["ids"] and results["ids"][0]:
            distances = (results.get("distances") or [[]])[0]
            for i, doc_id in enumerate(results["ids"][0]):
                doc = results["documents"][0][i] if results["documents"] else ""
                # Chroma 默认返回的是 distance（越小越像）；转成 0-1 的相似度便于设阈值
                distance = distances[i] if i < len(distances) else None
                similarity = None if distance is None else max(0.0, 1.0 - float(distance))
                memories.append({
                    "id": doc_id,
                    "one_line_summary": doc[:100] if doc else "",
                    "similarity": similarity,
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
        if not mem:
            mem = {
                "id": hit["id"], "task_type": "", "tags": [],
                "task_description": "", "quality_score": 5,
                "one_line_summary": hit["one_line_summary"],
                "successes": [], "lessons": [], "improvements": [],
            }
        quality = int(mem.get("quality_score") or 0)
        similarity = hit.get("similarity")
        if quality < MIN_QUALITY:
            safe_print(f"[RAG] 丢弃低质量经验 #{hit['id'][:8]}（质量 {quality} < {MIN_QUALITY}）")
            continue
        if similarity is not None and similarity < MIN_SCORE:
            safe_print(f"[RAG] 丢弃低相似经验 #{hit['id'][:8]}（{similarity:.2f} < {MIN_SCORE}）")
            continue
        mem = dict(mem)
        mem["similarity"] = similarity
        results.append(mem)

    if hits:
        best = max((h.get("similarity") or 0) for h in hits)
        safe_print(f"[RAG] 命中 {len(results)} 条历史经验（最高相似度 {best:.2f}）")
    return results


def format_memories_for_context(memories: list[dict]) -> str:
    """将检索到的经验格式化为可供 Agent 参考的上下文。"""
    if not memories:
        return ""
    lines = ["", "## 历史经验参考", "以下是从历史任务中检索出的相关经验：", ""]
    for i, mem in enumerate(memories, 1):
        src = f"[来源: 记忆#{mem.get('id', '?')}"
        if mem.get("created_at"):
            src += f" · 沉淀于 {mem['created_at']}"
        src += "]"
        lines.append(
            f"### 经验 {i}: {mem.get('one_line_summary', mem.get('task_description', '')[:80])}"
        )
        lines.append(src)
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
