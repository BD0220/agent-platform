"""服务层冒烟测试 —— 之前这一层是**零测试**，全靠手工脚本点。

覆盖的都是"上线后最容易坏、坏了最难看"的接口：
    * 静态前端能不能正常吐出来（漏拷 frontend/ 时容器里会静默变成只有 /docs）；
    * 会话列表 / 详情 / 404 语义；
    * 统计与计量接口的字段形状；
    * 交付物读取的越权拦截（必须 403，不能把整个磁盘读出去）；
    * 参数校验（空消息不能建会话）。

这些用例不碰 LLM：只验证 HTTP 面与鉴权边界，CI 里可离线跑。
"""
import os
import sys
import tempfile

# 必须在 import agent_platform 之前指定数据目录，避免污染真实 data/
_TMP = tempfile.mkdtemp(prefix="smoke_test_")
os.environ.setdefault("AGENT_DATA_DIR", _TMP)
os.environ.setdefault("AGENT_DELIVERIES_DIR", os.path.join(_TMP, "deliveries"))
os.makedirs(os.environ["AGENT_DELIVERIES_DIR"], exist_ok=True)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pytest  # noqa: E402
from agent_platform.server.api import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_index_serves_frontend(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert "/js/md.js" in body, "前端必须加载 Markdown 渲染器"
    assert "/js/chat.js" in body


def test_static_assets_reachable(client):
    for path in ("/js/md.js", "/js/chat.js", "/js/app.js", "/css/style.css"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert len(resp.text) > 200, path


def test_sessions_list_shape(client):
    resp = client.get("/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert isinstance(data["sessions"], list)


def test_unknown_session_is_404(client):
    resp = client.get("/sessions/does-not-exist")
    assert resp.status_code == 404


def test_stats_and_metrics(client):
    for path in ("/stats", "/metrics"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert isinstance(resp.json(), dict)


def test_start_session_rejects_empty_message(client):
    resp = client.post("/sessions/start", json={"message": "   "})
    assert resp.status_code in (400, 422)


def test_file_content_denies_path_outside_workspace(client):
    """交付物读取必须限定在已登记工作空间内，越权要 403。"""
    outside = "C:\\Windows\\win.ini" if os.name == "nt" else "/etc/passwd"
    resp = client.get("/files/content", params={"path": outside})
    assert resp.status_code == 403
    assert "工作空间" in resp.text


def test_file_tree_denies_path_outside_workspace(client):
    outside = "C:\\Windows" if os.name == "nt" else "/etc"
    resp = client.get("/files/tree", params={"path": outside})
    assert resp.status_code == 403
