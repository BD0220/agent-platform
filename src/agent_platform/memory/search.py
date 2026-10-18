"""记忆检索：ChromaDB 语义搜索优先，SQLite 关键词匹配兜底。"""
import re

from ..storage.vector_store import get_chroma_collection
from ..storage.database import db_search_memories
from ..utils.safe_print import safe_print
from .extraction import TYPE_KEYWORDS


def search_memory(query: str, top_k: int = 3, min_score: float = 1.0) -> list[dict]:
    """语义检索最相关的历史经验。ChromaDB 优先，降级关键词。"""
    coll = get_chroma_collection()
    if coll and coll.count() > 0:
        try:
            results = coll.query(query_texts=[query], n_results=min(top_k, coll.count()))
            memories = []
            if results and results["ids"] and results["ids"][0]:
                for i, doc_id in enumerate(results["ids"][0]):
                    meta = results["metadatas"][0][i] if results["metadatas"] else {}
                    doc = results["documents"][0][i] if results["documents"] else ""
                    distance = results["distances"][0][i] if results.get("distances") else 1.0
                    score = 1.0 - distance
                    tags = meta.get("tags", "").split(",") if meta.get("tags") else []
                    memories.append({
                        "id": doc_id, "task_type": meta.get("task_type", ""), "tags": tags,
                        "task_description": "", "quality_score": meta.get("quality_score", 5),
                        "one_line_summary": doc[:100] if doc else "",
                        "successes": [], "lessons": [], "improvements": [],
                        "_score": score, "_source": "chromadb",
                    })
            if memories:
                safe_print(f"[记忆系统] ChromaDB 语义检索命中 {len(memories)} 条")
                return memories
        except Exception as e:
            safe_print(f"[记忆系统] ChromaDB 检索失败，降级关键词: {e}")

    return search_memories(query, top_k=top_k, min_score=min_score)


def _keyword_score(memory: dict, query: str) -> float:
    score = 0.0
    for tag in memory.get("tags", []):
        if tag.lower() in query.lower():
            score += 3.0
        for kw in TYPE_KEYWORDS.get(tag, []):
            if kw.lower() in query.lower():
                score += 1.5
    desc = memory.get("task_description", "")
    query_words = set(re.findall(r'[一-鿿\w]+', query.lower()))
    desc_words = set(re.findall(r'[一-鿿\w]+', desc.lower()))
    score += len(query_words & desc_words) * 0.8
    summary = memory.get("one_line_summary", "")
    summary_words = set(re.findall(r'[一-鿿\w]+', summary.lower()))
    score += len(query_words & summary_words) * 0.5
    quality = memory.get("quality_score", 5)
    score *= (0.8 + quality / 50)
    return score


def search_memories(user_request: str, top_k: int = 3, min_score: float = 1.0) -> list[dict]:
    """关键词匹配检索（ChromaDB 降级方案，使用 SQLite）。"""
    memories = db_search_memories(limit=10000)
    if not memories:
        return []

    scored = []
    for mem in memories:
        s = _keyword_score(mem, user_request)
        if s >= min_score:
            scored.append((s, mem))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [mem for _, mem in scored[:top_k]]


def format_memories_for_context(memories: list[dict]) -> str:
    """格式化为可供主Agent 参考的上下文字符串。"""
    if not memories:
        return ""

    lines = ["", "## 历史经验参考（来自记忆库）", "以下是从历史任务中沉淀的经验，请在执行当前任务时参考：", ""]

    for i, mem in enumerate(memories, 1):
        lines.append(f"### 经验 {i}: {mem.get('one_line_summary', mem.get('task_description', '')[:80])}")
        lines.append(f"- **任务类型**: {', '.join(mem.get('tags', []))}")
        lines.append(f"- **质量评分**: {mem.get('quality_score', '?')}/10")
        lines.append(f"- **原始任务**: {mem.get('task_description', '')[:150]}")

        successes = mem.get("successes", [])
        if successes:
            lines.append(f"- **成功之处**: {'; '.join(successes)}")
        lessons = mem.get("lessons", [])
        if lessons:
            lines.append(f"- **失败教训**: {'; '.join(lessons)}")
        improvements = mem.get("improvements", [])
        if improvements:
            lines.append(f"- **改进建议**: {'; '.join(improvements)}")
        lines.append("")

    lines.append("**请参考上述经验，避免重复已知错误，借鉴成功做法。**")
    return "\n".join(lines)
