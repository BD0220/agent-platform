"""长期记忆：ChromaDB 向量存储 + JSON 文件双写。"""
import json
import shutil
from pathlib import Path

from ..storage.vector_store import get_chroma_collection
from ..utils.paths import DATA_DIR, PROJECT_ROOT
from ..utils.safe_print import safe_print

_NEW_PATH = DATA_DIR / "memory.json"
_OLD_PATH = PROJECT_ROOT / "memory.json"


def _resolve_memory_path() -> Path:
    """迁移：优先使用 data/memory.json，自动从根目录迁移旧数据。"""
    if _NEW_PATH.exists():
        return _NEW_PATH
    if _OLD_PATH.exists():
        _NEW_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(_OLD_PATH), str(_NEW_PATH))
        safe_print(f"[长期记忆] 已从 {_OLD_PATH} 迁移到 {_NEW_PATH}")
        return _NEW_PATH
    return _NEW_PATH


MEMORY_FILE = _resolve_memory_path()


def _experience_to_text(exp: dict) -> str:
    parts = [
        f"任务类型: {exp.get('task_type', '')}",
        f"标签: {', '.join(exp.get('tags', []))}",
        f"描述: {exp.get('task_description', '')}",
        f"总结: {exp.get('one_line_summary', '')}",
        f"成功: {'; '.join(exp.get('successes', []))}",
        f"教训: {'; '.join(exp.get('lessons', []))}",
        f"改进: {'; '.join(exp.get('improvements', []))}",
    ]
    return "\n".join(parts)


def _add_to_chroma(exp: dict):
    coll = get_chroma_collection()
    if not coll:
        return
    try:
        text = _experience_to_text(exp)
        metadata = {
            "task_type": exp.get("task_type", ""),
            "tags": ",".join(exp.get("tags", [])),
            "quality_score": exp.get("quality_score", 5),
            "created_at": exp.get("created_at", ""),
        }
        coll.upsert(ids=[exp["id"]], documents=[text], metadatas=[metadata])
    except Exception as e:
        safe_print(f"[长期记忆] ChromaDB 写入失败: {e}")


def _load_memories_json() -> dict:
    if not MEMORY_FILE.exists():
        return {"memories": [], "stats": {"total": 0}}
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {"memories": [], "stats": {"total": 0}}


def _save_memories_json(data: dict):
    data["stats"] = {
        "total": len(data.get("memories", [])),
        "last_updated": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except (UnicodeEncodeError, UnicodeDecodeError):
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=True, indent=2)


def _sync_json_to_chroma():
    """将 memory.json 中所有经验同步到 ChromaDB（一次性迁移）。"""
    data = _load_memories_json()
    memories = data.get("memories", [])
    if not memories:
        return
    coll = get_chroma_collection()
    if not coll:
        return
    try:
        existing = coll.get()
        existing_ids = set(existing["ids"]) if existing and existing["ids"] else set()
        new_exps = [m for m in memories if m["id"] not in existing_ids]
        if new_exps:
            for exp in new_exps:
                _add_to_chroma(exp)
            safe_print(f"[长期记忆] 已同步 {len(new_exps)} 条 JSON 经验到 ChromaDB")
    except Exception as e:
        safe_print(f"[长期记忆] 同步失败: {e}")
