"""MCP (Model Context Protocol) 桥接层 —— 连接外部 MCP Server。"""
import json
import os
import subprocess
import threading

from ..utils.safe_print import safe_print


class MCPClient:
    """简易 MCP 客户端，通过 subprocess 启动 MCP Server，JSON-RPC over stdio 通信。"""

    def __init__(self, name: str, command: list[str], env: dict = None):
        self.name = name
        self.command = command
        self.env = env
        self.process = None
        self._lock = threading.Lock()
        self._request_id = 0
        self.tools: list[dict] = []

    def connect(self) -> bool:
        try:
            self.process = subprocess.Popen(
                self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True,
                env={**os.environ, **(self.env or {})},
            )
            response = self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "agent-platform", "version": "1.0"},
            })
            if response and "error" not in response:
                tools_response = self._send_request("tools/list", {})
                if tools_response and "result" in tools_response:
                    self.tools = tools_response["result"].get("tools", [])
                    safe_print(f"[MCP] {self.name}: 发现 {len(self.tools)} 个工具")
                return True
            return False
        except Exception as e:
            safe_print(f"[MCP] {self.name} 连接失败: {e}")
            return False

    def disconnect(self):
        if self.process:
            try:
                self.process.stdin.close()
                self.process.stdout.close()
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                self.process.kill()
            self.process = None

    def _send_request(self, method: str, params: dict) -> dict | None:
        if not self.process or self.process.poll() is not None:
            return None
        with self._lock:
            self._request_id += 1
            request = {"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params}
            try:
                self.process.stdin.write(json.dumps(request) + "\n")
                self.process.stdin.flush()
                response_str = self.process.stdout.readline()
                if response_str:
                    return json.loads(response_str)
            except Exception:
                return None
        return None

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        response = self._send_request("tools/call", {"name": tool_name, "arguments": arguments})
        if response and "result" in response:
            content = response["result"].get("content", [])
            if isinstance(content, list):
                texts = []
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        texts.append(c.get("text", ""))
                    elif isinstance(c, str):
                        texts.append(c)
                return "\n".join(texts)
            return str(content)
        elif response and "error" in response:
            return f"[MCP 错误] {response['error'].get('message', str(response['error']))}"
        return "[MCP 错误] 无响应"


_mcp_clients: dict[str, MCPClient] = {}


def connect_mcp_server(name: str, command: list[str], env: dict = None) -> bool:
    if name in _mcp_clients:
        return True
    client = MCPClient(name, command, env)
    if not client.connect():
        return False
    _mcp_clients[name] = client

    try:
        from ..tools.registry import register_tool

        for tool in client.tools:
            tool_name = f"mcp_{name}_{tool['name']}"
            tool_desc = tool.get("description", f"MCP 工具: {tool['name']}")
            input_schema = tool.get("inputSchema", {"type": "object", "properties": {}, "required": []})

            def make_executor(c, t_name):
                def executor(params_str, content_str):
                    try:
                        args = json.loads(params_str) if params_str else {}
                    except json.JSONDecodeError:
                        args = {}
                    if content_str:
                        args["content"] = content_str
                    return c.call_tool(t_name, args)
                return executor

            register_tool(tool_name, tool_desc, json.dumps(input_schema.get("properties", {}), ensure_ascii=False),
                         make_executor(client, tool["name"]),
                         schema={"type": "function", "function": {"name": tool_name, "description": tool_desc,
                                  "parameters": input_schema}})
    except ImportError:
        pass

    safe_print(f"[MCP] {name} 已连接并注册 {len(client.tools)} 个工具")
    return True


def disconnect_mcp_server(name: str):
    client = _mcp_clients.pop(name, None)
    if client:
        client.disconnect()
        safe_print(f"[MCP] {name} 已断开")


def list_mcp_servers() -> list[dict]:
    return [{"name": name, "tools_count": len(c.tools), "command": c.command} for name, c in _mcp_clients.items()]


def shutdown_all():
    for name in list(_mcp_clients.keys()):
        disconnect_mcp_server(name)
