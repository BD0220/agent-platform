"""abundance 启动器 —— 单端口网页版。

只做三件事：
1. 把回环地址塞进 NO_PROXY（开着 VPN 时，urllib/requests 连 127.0.0.1
   会被代理拦成 502，整个应用"看起来坏了"—— 这道保险必须在任何网络调用之前）。
2. 用 uvicorn 把 FastAPI 跑起来（API + 静态前端同一个端口，默认 8000）。
3. 服务就绪后用默认浏览器打开页面。

原来那个双进程 / 桌面 App 窗口的形态已经移除，以网页端为主。
"""
import os
import sys
import threading
import time
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "backend")

HOST = os.getenv("ABUNDANCE_HOST", "127.0.0.1")
PORT = int(os.getenv("ABUNDANCE_PORT", "8000"))


def _bypass_proxy_for_localhost():
    """把本机回环地址加进 NO_PROXY，不影响对外部 API 的代理。"""
    for var in ("NO_PROXY", "no_proxy"):
        hosts = [h.strip() for h in os.environ.get(var, "").split(",") if h.strip()]
        for h in ("127.0.0.1", "localhost", "::1"):
            if h not in hosts:
                hosts.append(h)
        os.environ[var] = ",".join(hosts)


def _load_env_file():
    """把项目根目录 .env 装进环境变量（已存在的变量不覆盖）。

    为什么不用 python-dotenv：就十几行的事，不值得多一个依赖。
    支持 KEY=VALUE、# 注释、行内 # 注释；值上的引号会被剥掉。
    """
    path = os.path.join(ROOT, ".env")
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.split(" #")[0].strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _setup_paths():
    for d in (".data", ".data/sessions", "deliveries"):
        os.makedirs(os.path.join(ROOT, d), exist_ok=True)
    if SRC not in sys.path:
        sys.path.insert(0, SRC)
    os.chdir(ROOT)
    _load_env_file()
    _bypass_proxy_for_localhost()


def _open_when_ready(url: str, timeout: float = 60.0):
    """等服务真的能连上再开浏览器，避免弹出一个"无法访问此网站"。"""
    import socket
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.4)
                if s.connect_ex((HOST, PORT)) == 0:
                    break
        except OSError:
            pass
        time.sleep(0.3)
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"{url}/docs", timeout=2) as r:
            if r.status != 200:
                return
        webbrowser.open(url)
    except Exception:
        # 探活失败就不强行开页面，服务本身照常在跑
        pass


def main():
    _setup_paths()

    import uvicorn

    url = f"http://{HOST}:{PORT}"
    print("=" * 52)
    print("abundance  starting ...")
    print(f"frontend {url}")
    print("Ctrl+C to stop")
    print("=" * 52)

    threading.Thread(target=_open_when_ready, args=(url,),
                     name="abundance-open", daemon=True).start()

    uvicorn.run(
        "agent_platform.server.api:app",
        host=HOST, port=PORT,
        log_level="warning", access_log=False,
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[abundance] stopped")
        sys.exit(0)
