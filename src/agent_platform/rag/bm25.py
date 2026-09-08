"""BM25 关键词索引（轻量级，纯 Python）。"""
import re

from ..storage.database import db_get_all_memories
from ..utils.safe_print import safe_print


class BM25Index:
    """简易 BM25 索引，用于关键词检索。"""

    def __init__(self):
        self.documents: list[dict] = []
        self._built = False

    def add(self, doc_id: str, text: str, metadata: dict = None):
        self.documents.append({"id": doc_id, "text": text, "metadata": metadata or {}})
        self._built = False

    def _tokenize(self, text: str) -> list[str]:
        tokens = []
        en_tokens = re.findall(r'[a-zA-Z]+', text.lower())
        tokens.extend(en_tokens)
        cn_chars = re.findall(r'[一-鿿]+', text)
        for segment in cn_chars:
            for i in range(len(segment)):
                if i + 2 <= len(segment):
                    tokens.append(segment[i:i + 2])
                if i + 3 <= len(segment):
                    tokens.append(segment[i:i + 3])
        tokens.extend(re.findall(r'[一-鿿]', text))
        return tokens

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        if not self.documents:
            return []

        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        N = len(self.documents)
        df = {token: sum(1 for d in self.documents if token in d["text"].lower()) for token in query_tokens}

        k1, b = 1.5, 0.75
        avg_dl = sum(len(d["text"]) for d in self.documents) / N if N > 0 else 1

        scored = []
        for doc in self.documents:
            score = 0.0
            doc_text = doc["text"].lower()
            doc_len = len(doc["text"])
            doc_tokens = self._tokenize(doc_text)
            tf = {}
            for t in doc_tokens:
                tf[t] = tf.get(t, 0) + 1

            for token in query_tokens:
                if token in tf:
                    idf = max(0, (N - df.get(token, 0) + 0.5) / (df.get(token, 0) + 0.5))
                    numerator = tf[token] * (k1 + 1)
                    denominator = tf[token] + k1 * (1 - b + b * doc_len / avg_dl)
                    score += idf * numerator / denominator

            if score > 0:
                scored.append({"id": doc["id"], "score": score, "metadata": doc.get("metadata", {})})

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    def rebuild(self):
        self._built = True


_bm25_index = BM25Index()


def _build_bm25():
    """从 SQLite 构建 BM25 索引。"""
    memories = db_get_all_memories()
    if not memories:
        return

    for mem in memories:
        text = (
            f"{mem.get('task_type', '')} "
            f"{' '.join(mem.get('tags', []))} "
            f"{mem.get('task_description', '')} "
            f"{mem.get('one_line_summary', '')} "
            f"{' '.join(mem.get('successes', []))} "
            f"{' '.join(mem.get('lessons', []))} "
            f"{' '.join(mem.get('improvements', []))}"
        )
        _bm25_index.add(mem["id"], text, {
            "task_type": mem.get("task_type", ""),
            "quality_score": mem.get("quality_score", 5),
            "one_line_summary": mem.get("one_line_summary", ""),
        })

    _bm25_index.rebuild()
    safe_print(f"[RAG] BM25 索引已构建，共 {len(_bm25_index.documents)} 篇文档")
