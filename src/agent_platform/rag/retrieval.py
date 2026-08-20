"""高级 RAG 检索：BM25 + ChromaDB 稠密向量 + RRF 融合 + LLM 重排序 + 查询扩展。"""
import json
import re

from ..storage.vector_store import get_chroma_collection
from ..storage.database import db_search_memories
from ..utils.safe_print import safe_print
from .bm25 import _bm25_index


def _dense_search(query: str, top_k: int = 5) -> list[dict]:
    coll = get_chroma_collection()
    if not coll or coll.count() == 0:
        return []
    try:
        results = coll.query(query_texts=[query], n_results=min(top_k, coll.count()))
        memories = []
        if results and results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                meta = results["metadatas"][0][i] if results["metadatas"] else {}
                doc = results["documents"][0][i] if results["documents"] else ""
                distance = results["distances"][0][i] if results.get("distances") else 1.0
                memories.append({
                    "id": doc_id, "score": 1.0 - distance,
                    "metadata": {"task_type": meta.get("task_type", ""),
                                 "quality_score": meta.get("quality_score", 5),
                                 "one_line_summary": doc[:100] if doc else ""},
                    "source": "dense",
                })
        return memories
    except Exception as e:
        safe_print(f"[RAG] 稠密检索失败: {e}")
        return []


def _reciprocal_rank_fusion(results_list: list[list[dict]], k: int = 60) -> list[dict]:
    fused: dict[str, dict] = {}
    for results in results_list:
        for rank, item in enumerate(results):
            doc_id = item["id"]
            rrf_score = 1.0 / (k + rank + 1)
            if doc_id in fused:
                fused[doc_id]["score"] += rrf_score
            else:
                fused[doc_id] = {
                    "id": doc_id, "score": rrf_score,
                    "metadata": item.get("metadata", {}),
                    "sources": [item.get("source", "unknown")],
                }
    return sorted(fused.values(), key=lambda x: x["score"], reverse=True)


def _llm_rerank(query: str, candidates: list[dict], top_k: int = 3) -> list[dict]:
    if len(candidates) <= top_k:
        return candidates
    try:
        from ..llm.factory import get_llm
        llm = get_llm()

        candidates_text = []
        for i, c in enumerate(candidates):
            meta = c.get("metadata", {})
            candidates_text.append(
                f"[{i}] 类型: {meta.get('task_type', '')} | "
                f"摘要: {meta.get('one_line_summary', '')[:120]}"
            )

        prompt = (
            f"请评估以下记忆条目与查询「{query[:200]}」的相关性。\n"
            f"对每条给出 1-10 的评分（只输出 JSON 数组，不要额外文字）。\n\n"
            + "\n".join(candidates_text) +
            "\n\n输出格式: [{\"index\": 0, \"score\": 8, \"reason\": \"...\"}, ...]"
        )

        result = llm.chat(messages=[
            {"role": "system", "content": "你是一个检索质量评估专家。只输出 JSON。"},
            {"role": "user", "content": prompt},
        ], temperature=0.1)

        content = result.content.strip()
        json_match = re.search(r'\[[\s\S]*\]', content)
        if json_match:
            ratings = json.loads(json_match.group())
            rating_map = {r.get("index", i): r.get("score", 5) for i, r in enumerate(ratings)}
            for i, c in enumerate(candidates):
                c["rerank_score"] = rating_map.get(i, 5)
                c["score"] = c.get("score", 0) * (0.5 + 0.05 * c["rerank_score"])
        candidates.sort(key=lambda x: x["score"], reverse=True)
    except Exception as e:
        safe_print(f"[RAG] LLM 重排序失败，使用原始排序: {e}")
    return candidates[:top_k]


def _expand_query(query: str) -> list[str]:
    queries = [query]
    try:
        from ..llm.factory import get_llm
        llm = get_llm()
        result = llm.chat(messages=[
            {"role": "system", "content": "你是一个查询扩展专家。对给定查询生成 2-3 个不同角度的表述变体，用于提高检索召回率。只输出 JSON 数组，不要额外文字。"},
            {"role": "user", "content": f"请为以下查询生成变体：{query[:300]}"},
        ], temperature=0.7)
        content = result.content.strip()
        json_match = re.search(r'\[[\s\S]*\]', content)
        if json_match:
            expanded = json.loads(json_match.group())
            if isinstance(expanded, list):
                queries.extend([q for q in expanded if isinstance(q, str) and q != query])
    except Exception as e:
        safe_print(f"[RAG] 查询扩展失败: {e}")
    return queries[:4]


def advanced_search(query: str, top_k: int = 3, use_rerank: bool = True,
                    use_expansion: bool = True) -> list[dict]:
    """高级 RAG 检索：查询扩展 → 混合检索 → RRF 融合 → LLM 重排序。"""
    queries = _expand_query(query) if use_expansion else [query]
    safe_print(f"[RAG] 查询扩展: {len(queries)} 个变体")

    all_dense = []; all_bm25 = []
    for q in queries:
        all_dense.extend(_dense_search(q, top_k=top_k * 2))
        all_bm25.extend(_bm25_index.search(q, top_k=top_k * 2))

    fused = _reciprocal_rank_fusion([all_dense, all_bm25])
    safe_print(f"[RAG] RRF 融合: {len(fused)} 个候选")

    enriched = []; seen_ids = set()
    for item in fused:
        doc_id = item["id"]
        if doc_id in seen_ids:
            continue
        seen_ids.add(doc_id)

        memories = db_search_memories(keyword="", limit=500)
        full_mem = None
        for m in memories:
            if m["id"] == doc_id:
                full_mem = m; break

        if full_mem:
            full_mem["_score"] = item["score"]
            full_mem["_source"] = "+".join(item.get("sources", ["hybrid"]))
            enriched.append(full_mem)
        else:
            meta = item.get("metadata", {})
            enriched.append({
                "id": doc_id, "task_type": meta.get("task_type", ""), "tags": [],
                "task_description": "", "quality_score": meta.get("quality_score", 5),
                "one_line_summary": meta.get("one_line_summary", ""),
                "successes": [], "lessons": [], "improvements": [],
                "_score": item["score"], "_source": "+".join(item.get("sources", ["hybrid"])),
            })

    if use_rerank and len(enriched) > top_k:
        enriched = _llm_rerank(query, enriched, top_k)

    results = enriched[:top_k]
    if results:
        safe_print(f"[RAG] 高级检索命中 {len(results)} 条, 来源: {set(r.get('_source', '?') for r in results)}")
    else:
        safe_print("[RAG] 未找到相关结果")
    return results


def index_document(doc_id: str, text: str, metadata: dict = None):
    _bm25_index.add(doc_id, text, metadata)
    safe_print(f"[RAG] 已索引文档: {doc_id}")


def format_memories_for_context(memories: list[dict]) -> str:
    """将检索到的记忆格式化为可供 Agent 参考的上下文。"""
    if not memories:
        return ""
    lines = ["", "## 历史经验参考（高级 RAG 检索）", "以下是从历史任务中语义检索的经验：", ""]
    for i, mem in enumerate(memories, 1):
        source = mem.get("_source", "memory")
        score = mem.get("_score", 0)
        lines.append(
            f"### 经验 {i}: {mem.get('one_line_summary', mem.get('task_description', '')[:80])} "
            f"(相关度: {score:.2f}, 来源: {source})"
        )
        lines.append(f"- **任务类型**: {', '.join(mem.get('tags', [mem.get('task_type', '')]))}")
        lines.append(f"- **质量评分**: {mem.get('quality_score', '?')}/10")
        lines.append(f"- **原始任务**: {mem.get('task_description', '')[:150]}")
        successes = mem.get("successes", [])
        if successes:
            lines.append(f"- **成功之处**: {'; '.join(successes[:3])}")
        lessons = mem.get("lessons", [])
        if lessons:
            lines.append(f"- **失败教训**: {'; '.join(lessons[:3])}")
        improvements = mem.get("improvements", [])
        if improvements:
            lines.append(f"- **改进建议**: {'; '.join(improvements[:3])}")
        lines.append("")
    lines.append("**请参考上述经验，避免重复已知错误，借鉴成功做法。**")
    return "\n".join(lines)
