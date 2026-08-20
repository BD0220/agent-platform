"""Web 工具：web_search, fetch_url。"""
import re
import urllib.request
import urllib.parse


def _web_search(params: str, content: str) -> str:
    query = params.strip()
    if not query:
        return "错误: 请提供搜索关键词"
    try:
        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
        results = []
        for s in snippets[:5]:
            clean = re.sub(r'<[^>]+>', '', s).strip()
            if clean:
                results.append(f"{len(results) + 1}. {clean}")
        return "\n\n".join(results) if results else f"未找到与 '{query}' 相关的结果"
    except Exception as e:
        return f"搜索失败: {e}"


def _fetch_url(params: str, content: str) -> str:
    url = params.strip()
    if not url:
        return "错误: 请提供 URL"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")[:5000]
        return f"状态码: {resp.status}\n内容:\n{body}"
    except Exception as e:
        return f"请求失败: {e}"


def _map_web_search(tool_name: str, arguments: dict) -> tuple[str, str]:
    return arguments.get("query", ""), ""


def _map_fetch_url(tool_name: str, arguments: dict) -> tuple[str, str]:
    return arguments.get("url", ""), ""
