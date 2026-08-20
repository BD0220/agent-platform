"""编码安全打印 —— 全项目唯一一份，其他模块统一 import 此处。"""
import sys


def safe_print(*args, **kwargs):
    """print 的编码安全包装，避免 emoji 等字符在 GBK 终端下崩溃。"""
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        text = " ".join(str(a) for a in args)
        print(text.encode(sys.stdout.encoding or "utf-8", errors="replace")
              .decode(sys.stdout.encoding or "utf-8", errors="replace"), **kwargs)
