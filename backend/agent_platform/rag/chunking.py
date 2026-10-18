"""文档分块：递归语义分块，500 字/块，100 字重叠。"""
import re


def recursive_chunk(text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
    """递归语义分块：按段落 → 按句子 → 按固定大小切分。"""
    if len(text) <= chunk_size:
        return [text] if text.strip() else []

    paragraphs = re.split(r'\n{2,}', text)
    chunks = []

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if len(para) <= chunk_size:
            chunks.append(para)
        else:
            # 按句子切分
            sentences = re.split(r'(?<=[。！？.!?])\s*', para)
            current = ""
            for sent in sentences:
                sent = sent.strip()
                if not sent:
                    continue

                if len(current) + len(sent) <= chunk_size:
                    current = (current + " " + sent).strip() if current else sent
                else:
                    if current:
                        chunks.append(current)
                    # 超长句硬切
                    if len(sent) > chunk_size:
                        sub_chunks = _hard_split(sent, chunk_size, overlap)
                        chunks.extend(sub_chunks)
                        current = ""
                    else:
                        current = sent
            if current:
                chunks.append(current)

    # 添加重叠
    if overlap > 0 and len(chunks) > 1:
        overlapped = [chunks[0]]
        for i in range(1, len(chunks)):
            prev = chunks[i - 1]
            curr = chunks[i]
            if len(prev) > overlap:
                overlap_text = prev[-overlap:]
                overlapped.append(overlap_text + " " + curr)
            else:
                overlapped.append(curr)
        return overlapped

    return chunks


def _hard_split(text: str, chunk_size: int, overlap: int) -> list[str]:
    """固定大小硬切分，带重叠。"""
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(text[start:end].strip())
        start += chunk_size - overlap
    return chunks
