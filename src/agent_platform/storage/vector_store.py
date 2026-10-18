"""全局唯一 ChromaDB 连接单例，由 memory 和 rag 模块共享。

路径从 ``utils.paths`` 获取，支持环境变量 ``AGENT_DATA_DIR`` 覆盖。
默认使用 ChromaDB 内置 embedding（ONNX miniLM），可通过环境变量
``AGENT_EMBEDDING_MODEL`` 指定其他模型。
"""
from ..utils.paths import CHROMA_DIR
from ..utils.safe_print import safe_print

_chroma_client = None
_chroma_collection = None
_chroma_available = None


def get_chroma_collection():
    """懒加载 ChromaDB collection，不可用时返回 None。全局唯一实例。"""
    global _chroma_client, _chroma_collection, _chroma_available
    if _chroma_available is False:
        return None
    if _chroma_collection is not None:
        return _chroma_collection
    try:
        import chromadb
        from chromadb.config import Settings

        _chroma_client = chromadb.PersistentClient(
            path=CHROMA_DIR,
            settings=Settings(anonymized_telemetry=False, allow_reset=True),
        )
        _chroma_collection = _chroma_client.get_or_create_collection(
            name="agent_experiences",
            metadata={"hnsw:space": "cosine"},
        )
        _chroma_available = True
        count = _chroma_collection.count()
        safe_print(f"[向量存储] ChromaDB 连接成功 (共 {count} 条)")
        return _chroma_collection
    except Exception as e:
        _chroma_available = False
        safe_print(f"[向量存储] ChromaDB 不可用 ({e})")
        return None


def get_chroma_client():
    """获取 ChromaDB 客户端实例，未初始化时先初始化。"""
    if _chroma_client is None:
        get_chroma_collection()
    return _chroma_client


def get_or_create_kb_collection(name: str):
    """获取或创建知识库专用 collection。"""
    global _chroma_client
    if _chroma_client is None:
        get_chroma_collection()
    if _chroma_client is None:
        return None
    try:
        coll = _chroma_client.get_or_create_collection(
            name=f"kb_{name}",
            metadata={"hnsw:space": "cosine"},
        )
        return coll
    except Exception:
        return None
