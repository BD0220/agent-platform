"""知识库导入：解析文件 → 分块 → Embed → 存入 ChromaDB + BM25。"""
import json
import os
import re
import uuid
from pathlib import Path

from ..utils.safe_print import safe_print
from ..storage.vector_store import get_or_create_kb_collection
from .chunking import recursive_chunk

SUPPORTED_EXTENSIONS = {".md", ".txt", ".py", ".json", ".yaml", ".yml", ".rst", ".log", ".csv"}


def _parse_file(file_path: str) -> str | None:
    ext = Path(file_path).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        try:
            with open(file_path, "r", encoding="gbk") as f:
                return f.read()
        except Exception:
            return None
    except Exception:
        return None


def import_file(file_path: str, collection_name: str = "default") -> int:
    """导入单个文件到知识库。返回导入的 chunk 数量。"""
    text = _parse_file(file_path)
    if text is None:
        safe_print(f"[KB导入] 不支持的文件类型: {file_path}")
        return 0

    filename = Path(file_path).name
    chunks = recursive_chunk(text)
    if not chunks:
        return 0

    coll = get_or_create_kb_collection(collection_name)
    if coll is None:
        safe_print("[KB导入] ChromaDB 不可用，无法导入")
        return 0

    ids = []
    documents = []
    metadatas = []
    for i, chunk in enumerate(chunks):
        chunk_id = f"kb_{collection_name}_{filename}_{uuid.uuid4().hex[:8]}"
        ids.append(chunk_id)
        documents.append(chunk)
        metadatas.append({
            "source_file": filename,
            "source_path": str(file_path),
            "chunk_index": i,
            "collection": collection_name,
        })

    try:
        coll.upsert(ids=ids, documents=documents, metadatas=metadatas)
        from .bm25 import _bm25_index
        for i, chunk in enumerate(chunks):
            _bm25_index.add(ids[i], chunk, metadatas[i])
        safe_print(f"[KB导入] {filename} → {len(chunks)} chunks → collection '{collection_name}'")
        return len(chunks)
    except Exception as e:
        safe_print(f"[KB导入] 写入失败: {e}")
        return 0


def import_directory(dir_path: str, collection_name: str = "default") -> int:
    """递归导入目录中所有支持的文件。返回总 chunk 数。"""
    total = 0
    for root, dirs, files in os.walk(dir_path):
        for f in files:
            ext = Path(f).suffix.lower()
            if ext in SUPPORTED_EXTENSIONS:
                file_path = os.path.join(root, f)
                total += import_file(file_path, collection_name)
    safe_print(f"[KB导入] 目录 '{dir_path}' 导入完成，共 {total} chunks → '{collection_name}'")
    return total


def import_text(text: str, title: str, collection_name: str = "default") -> int:
    """导入纯文本内容到知识库。返回 chunk 数。"""
    chunks = recursive_chunk(text)
    if not chunks:
        return 0

    coll = get_or_create_kb_collection(collection_name)
    if coll is None:
        safe_print("[KB导入] ChromaDB 不可用，无法导入")
        return 0

    safe_title = re.sub(r'[\\/:*?"<>|]', '_', title)[:50]
    ids = []
    documents = []
    metadatas = []
    for i, chunk in enumerate(chunks):
        chunk_id = f"kb_{collection_name}_{safe_title}_{uuid.uuid4().hex[:8]}"
        ids.append(chunk_id)
        documents.append(chunk)
        metadatas.append({
            "source_file": safe_title,
            "source_path": title,
            "chunk_index": i,
            "collection": collection_name,
        })

    try:
        coll.upsert(ids=ids, documents=documents, metadatas=metadatas)
        from .bm25 import _bm25_index
        for i, chunk in enumerate(chunks):
            _bm25_index.add(ids[i], chunk, metadatas[i])
        safe_print(f"[KB导入] '{title}' → {len(chunks)} chunks → '{collection_name}'")
        return len(chunks)
    except Exception as e:
        safe_print(f"[KB导入] 写入失败: {e}")
        return 0


def search_knowledge(query: str, collection_name: str = "default", top_k: int = 5) -> str:
    """搜索知识库，返回格式化的上下文。"""
    coll = get_or_create_kb_collection(collection_name)
    if coll is None or coll.count() == 0:
        return f"[知识库] collection '{collection_name}' 为空或不可用"

    try:
        results = coll.query(query_texts=[query], n_results=min(top_k, coll.count()))
        if not results or not results["ids"] or not results["ids"][0]:
            return f"[知识库] 未找到与 '{query}' 相关的内容"

        lines = [f"## 知识库搜索: {query} (collection: {collection_name})", ""]
        for i, doc_id in enumerate(results["ids"][0]):
            meta = results["metadatas"][0][i] if results["metadatas"] else {}
            doc = results["documents"][0][i] if results["documents"] else ""
            distance = results["distances"][0][i] if results.get("distances") else 1.0
            score = 1.0 - distance
            source = meta.get("source_file", meta.get("source_path", "unknown"))
            lines.append(f"### 结果 {i+1}: {source} (相关度: {score:.2f})")
            lines.append(doc[:500])
            lines.append("")
        return "\n".join(lines)
    except Exception as e:
        return f"[知识库] 搜索失败: {e}"


def list_kb_collections() -> list[dict]:
    """列出所有知识库 collection。"""
    from ..storage.vector_store import get_chroma_client
    client = get_chroma_client()
    if client is None:
        return []
    try:
        collections = client.list_collections()
        return [
            {"name": c.name.replace("kb_", ""),
             "count": c.count()}
            for c in collections if c.name.startswith("kb_")
        ]
    except Exception:
        return []


def delete_kb_collection(name: str) -> bool:
    """删除知识库 collection。"""
    from ..storage.vector_store import get_chroma_client
    client = get_chroma_client()
    if client is None:
        return False
    try:
        client.delete_collection(f"kb_{name}")
        safe_print(f"[KB] 已删除 collection: {name}")
        return True
    except Exception as e:
        safe_print(f"[KB] 删除失败: {e}")
        return False
