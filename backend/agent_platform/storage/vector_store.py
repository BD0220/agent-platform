"""全局唯一 ChromaDB 连接单例，由 memory 和 rag 模块共享。

路径从 ``utils.paths`` 获取，支持环境变量 ``AGENT_DATA_DIR`` 覆盖。
默认使用 ChromaDB 内置 embedding（ONNX miniLM），可通过环境变量
``AGENT_EMBEDDING_MODEL`` 指定其他模型。
"""
import hashlib
import re

from ..utils.paths import CHROMA_DIR
from ..utils.safe_print import safe_print

_chroma_client = None
_chroma_collection = None
_chroma_available = None


def safe_collection_name(name: str) -> str:
    """把用户输入的知识库名净化成 ChromaDB 合法的 collection 名。

    ChromaDB 只认 ``[a-zA-Z0-9._-]`` 且必须以字母数字开头结尾 ——
    用户在界面上填中文名（如「我的知识库」）会直接报
    InvalidArgumentError。规则：非法字符替换为下划线、掐头去尾，
    再拼上原名的 md5 短哈希保证**确定性**（同名永远映射到同一个
    collection，且上传/搜索/删除四条路径映射一致）。
    """
    name = (name or "").strip()
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name).strip("._-")
    if not safe:
        safe = "kb"
    if safe != name or len(safe) < 3:
        safe = f"{safe[:40]}_{hashlib.md5(name.encode('utf-8')).hexdigest()[:8]}"
    if len(safe) < 3:
        safe += "kb"
    return safe


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
    """获取或创建知识库专用 collection（名字自动净化）。

    原始用户名存进 metadata 的 ``display_name``，列表页展示用它，
    这样中文名上传后还能原样显示回来。
    """
    global _chroma_client
    if _chroma_client is None:
        get_chroma_collection()
    if _chroma_client is None:
        return None
    try:
        coll = _chroma_client.get_or_create_collection(
            name=f"kb_{safe_collection_name(name)}",
            metadata={"hnsw:space": "cosine", "display_name": (name or "").strip()},
        )
        return coll
    except Exception as e:
        safe_print(f"[向量存储] 创建知识库 collection 失败: {e}")
        return None
