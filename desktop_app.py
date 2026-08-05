# -*- coding: utf-8 -*-
"""
OA运维智能Agent — 桌面版启动器 (PyWebView)
==========================================
双击桌面图标 -> 弹出原生应用窗口（Edge WebView2 内核），无浏览器、无控制台黑框。

用法:
    pythonw desktop_app.py     # 正式使用（无控制台）
    python   desktop_app.py    # 调试（带控制台日志）

特性:
    - 自动启动 FastAPI 后端 (main.py)，端口复用（已运行则直接开窗）
    - 启动画面轮询 /api/health，就绪后载入主界面
    - 关闭窗口 = 退出服务（仅退出本进程启动的后端）
    - 所有日志写入 data/desktop.log
"""

import os
import sys
import time
import socket
import logging
import subprocess
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
LOG_FILE = os.path.join(ROOT, "data", "desktop.log")
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    encoding="utf-8",
)
log = logging.getLogger("desktop")

def _read_port():
    """从 config.yaml 读取 server.port（yaml 失败时正则兜底）。"""
    cfg_path = os.path.join(ROOT, "config.yaml")
    try:
        import yaml
        with open(cfg_path, "r", encoding="utf-8") as _f:
            _cfg = yaml.safe_load(_f) or {}
        if isinstance(_cfg, dict):
            return int(_cfg.get("server", {}).get("port", 7860))
    except Exception:  # noqa: BLE001
        pass
    try:
        import re
        with open(cfg_path, "r", encoding="utf-8") as _f:
            _txt = _f.read()
        m = re.search(r"^\s*port:\s*(\d+)\s*$", _txt, re.M)
        if m:
            return int(m.group(1))
    except Exception:  # noqa: BLE001
        pass
    return 7860


PORT = _read_port()

BASE_URL = "http://127.0.0.1:%d" % PORT
HEALTH_URL = BASE_URL + "/api/health"
START_TIMEOUT = 180          # 后端就绪最长等待（秒）

_backend_proc = None
_spawned = False
_closed = False


def log_tail(n=30):
    """读取桌面日志末尾 n 行，用于错误页展示。"""
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-n:])
    except Exception:
        return ""


def port_in_use(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False


# 本地健康检查强制绕过系统代理（避免代理干扰 localhost 探测）
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def health_ok():
    try:
        with _OPENER.open(HEALTH_URL, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def start_backend():
    """启动后端；若服务已在运行（含正在启动中）则等待就绪后复用。"""
    global _backend_proc, _spawned
    if port_in_use(PORT):
        # 端口已被监听：等待最多 20s 确认是本系统服务（/api/health 就绪）后复用
        log.info("端口 %d 已有进程监听，等待服务就绪以便复用...", PORT)
        for _ in range(40):
            if health_ok():
                log.info("检测到服务已在运行 (端口 %s)，直接复用", PORT)
                return
            time.sleep(0.5)
        raise RuntimeError("端口 %d 已被其他程序占用，无法启动本系统服务" % PORT)
    log.info("启动后端: %s main.py --host 127.0.0.1 --port %d", sys.executable, PORT)
    _backend_log = open(os.path.join(ROOT, "data", "backend.log"), "ab", buffering=0)
    _backend_env = dict(os.environ)
    _backend_env["PYTHONIOENCODING"] = "utf-8"
    _backend_proc = subprocess.Popen(
        [sys.executable, "main.py", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=ROOT,
        env=_backend_env,
        stdout=_backend_log,
        stderr=subprocess.STDOUT,
    )
    _spawned = True


def stop_backend():
    """仅终止本进程启动的后端（复用场景下不动作）。"""
    global _backend_proc
    if _backend_proc is not None:
        log.info("正在停止后端...")
        try:
            _backend_proc.terminate()
            _backend_proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                _backend_proc.kill()
            except Exception:  # noqa: BLE001
                pass
        _backend_proc = None


def splash_html():
    return """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  html,body { height:100%; }
  body {
    background: radial-gradient(1200px 600px at 50% -10%, #2a2447 0%, #0f1115 55%);
    color:#e5e7eb; font-family:"Segoe UI","Microsoft YaHei",sans-serif;
    display:flex; flex-direction:column; align-items:center; justify-content:center;
    user-select:none;
  }
  .logo { font-size:60px; line-height:1; margin-bottom:16px; }
  .title { font-size:22px; font-weight:600; letter-spacing:2px; margin-bottom:6px; }
  .sub { font-size:13px; color:#8b8fa3; margin-bottom:32px; }
  .spinner {
    width:44px; height:44px; border-radius:50%;
    border:4px solid rgba(255,255,255,.15);
    border-top-color:#6366f1; animation:spin 1s linear infinite;
  }
  @keyframes spin { to { transform:rotate(360deg); } }
  #status { margin-top:22px; font-size:14px; color:#a5a9bd; }
  #detail { margin-top:8px; font-size:12px; color:#6b7280; max-width:520px; text-align:center; line-height:1.6; }
</style>
</head>
<body>
  <div class="logo">&#128737;</div>
  <div class="title">OA 运维智能 Agent</div>
  <div class="sub">本地服务启动中 · v2.5.0 桌面版</div>
  <div class="spinner"></div>
  <div id="status">正在启动服务...</div>
  <div id="detail">首次启动需加载本地嵌入模型，约 20~40 秒，请稍候</div>
</body>
</html>"""


def error_html(msg):
    import html as _h
    body = _h.escape(msg).replace("\n", "<br>")
    return """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  html,body { height:100%; }
  body {
    background:#0f1115; color:#e5e7eb;
    font-family:"Segoe UI","Microsoft YaHei",sans-serif;
    display:flex; align-items:center; justify-content:center; padding:40px;
  }
  .card { max-width:720px; width:100%; background:#1a1d26; border:1px solid #2c3140;
          border-radius:12px; padding:28px; }
  h2 { color:#f87171; font-size:18px; margin-bottom:14px; }
  .msg { font-size:13px; line-height:1.7; color:#a5a9bd; white-space:pre-wrap; word-break:break-all; }
</style>
</head>
<body>
  <div class="card">
    <h2>&#9888; 启动失败</h2>
    <div class="msg">%s</div>
  </div>
</body>
</html>""" % body


def bootstrap(window):
    """webview.start 启动后在线程中运行：拉起后端并等待就绪。"""
    t0 = time.time()
    try:
        start_backend()
    except Exception as e:  # noqa: BLE001
        log.error("启动失败: %s", e)
        window.load_html(error_html("启动失败：%s\n\n请查看 data/desktop.log" % e))
        return

    while not _closed and time.time() - t0 < START_TIMEOUT:
        if health_ok():
            log.info("服务就绪 (%.1fs)，载入主界面", time.time() - t0)
            window.load_url(BASE_URL + "/")
            return
        waited = int(time.time() - t0)
        try:
            js = "document.getElementById('status').textContent = '正在启动服务… 已等待 %d 秒';" % waited
            window.evaluate_js(js)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)

    if not _closed:
        msg = (
            "服务启动超时（> %d 秒）。\n\n请检查：\n"
            "  1) data/desktop.log 中的报错信息\n"
            "  2) 端口 %d 是否被其他程序占用\n\n"
            "最近日志：\n%s" % (START_TIMEOUT, PORT, log_tail(25))
        )
        log.error("服务启动超时")
        window.load_html(error_html(msg))


def on_closed():
    global _closed
    _closed = True
    log.info("窗口已关闭")


def main():
    import webview

    window = webview.create_window(
        "OA运维智能Agent",
        html=splash_html(),
        width=1280,
        height=820,
        min_size=(1024, 700),
        background_color="#0f1115",
    )
    try:
        window.events.closed += on_closed
    except Exception:  # noqa: BLE001
        pass

    try:
        webview.start(
            func=bootstrap,
            args=(window,),
            debug=False,
            private_mode=False,
            storage_path=os.path.join(ROOT, "data", "webview_profile"),
        )
    except Exception as e:  # noqa: BLE001
        log.error("webview 启动异常: %s", e)
        import webbrowser
        webbrowser.open(BASE_URL + "/")
    finally:
        stop_backend()
        log.info("桌面版已退出")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        log.error("桌面版异常退出: %s", e)
        try:
            import webbrowser
            webbrowser.open(BASE_URL + "/")
        except Exception:  # noqa: BLE001
            pass
        raise