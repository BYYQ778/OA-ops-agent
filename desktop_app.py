# -*- coding: utf-8 -*-
"""
OA运维智能Agent — 桌面版启动器 (PyWebView)
==========================================
双击桌面图标 -> 弹出原生应用窗口（Edge WebView2 内核），无浏览器、无控制台黑框。

支持两种运行方式：
  源码运行:   python desktop_app.py / pythonw desktop_app.py
  PyInstaller: 打包后的 OA运维Agent.exe（数据/配置/日志在 exe 同目录）

架构（进程隔离，避免 GUI 与 torch/onnxruntime 原生库冲突）：
  - 主进程：只跑 PyWebView 窗口（轻量）
  - 后端进程：自我派生（--backend 参数），运行 uvicorn + 全部 AI 依赖
  源码模式下后端子进程 = pythonw desktop_app.py --backend
  打包模式下后端子进程 = OA运维Agent.exe --backend

特性:
    - 端口复用（服务已运行则直接开窗，不重复启动后端）
    - 启动画面轮询 /api/health，就绪后载入主界面
    - 关闭窗口 = 优雅退出（终止后端子进程、释放端口）
    - 首次运行自动从内置资源生成 config.yaml / .env.example
    - 日志: data/desktop.log（桌面壳）、data/backend.log（后端/控制台输出）
"""

import os
import sys
import time
import socket
import logging
import subprocess
import urllib.request

# ========== 路径与冻结环境 ==========
FROZEN = getattr(sys, "frozen", False)
if FROZEN:
    APP_DIR = os.path.dirname(sys.executable)          # exe 所在目录（可写：数据/配置/日志）
    BUNDLE_DIR = getattr(sys, "_MEIPASS", APP_DIR)     # 解包资源目录（模板/静态/默认配置/图标/离线模型）
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = APP_DIR

os.chdir(APP_DIR)  # 统一工作目录，保证相对路径数据落在 APP_DIR 下

# pythonw / windowed exe 无控制台时，把 stdout/stderr 重定向到 backend.log
if sys.stdout is None or sys.stderr is None:
    os.makedirs(os.path.join(APP_DIR, "data"), exist_ok=True)
    _stdio = open(os.path.join(APP_DIR, "data", "backend.log"), "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = _stdio
    if sys.stderr is None:
        sys.stderr = _stdio

# 离线模型目录：优先使用打包内置的 models/hf（HF_HOME），找不到则用系统缓存
_models_dir = os.path.join(BUNDLE_DIR, "models", "hf")
if os.path.isdir(os.path.join(_models_dir, "hub")):
    os.environ["HF_HOME"] = _models_dir
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

# 方案A（外置业务代码）：优先从 exe 同目录 app/ 加载 ui/agents/utils/main，
# 更新这些模块无需重新打包（源码模式无 app/ 目录，走原有项目根路径）。
# 注意：必须在 BUNDLE_DIR 之后插入，确保 app 目录位于 sys.path 最前，
# 压过冻结在 PYZ 中的兜底副本（否则 PyiFrozenFinder 会优先命中 PYZ）。
sys.path.insert(0, BUNDLE_DIR)

EXTERNAL_APP_DIR = os.path.join(APP_DIR, "app")
if os.path.isdir(EXTERNAL_APP_DIR):
    sys.path.insert(0, EXTERNAL_APP_DIR)

# ========== 日志 ==========
os.makedirs(os.path.join(APP_DIR, "data"), exist_ok=True)
LOG_FILE = os.path.join(APP_DIR, "data", "desktop.log")
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    encoding="utf-8",
)
log = logging.getLogger("desktop")


def _ensure_config():
    """首次运行：把打包内置的 config.yaml / .env.example 复制到 APP_DIR。"""
    import shutil
    for name in ("config.yaml", ".env.example"):
        target = os.path.join(APP_DIR, name)
        bundled = os.path.join(BUNDLE_DIR, name)
        if not os.path.exists(target) and os.path.exists(bundled):
            try:
                shutil.copy2(bundled, target)
                log.info("已生成 %s（来自内置默认配置）", target)
            except Exception as e:  # noqa: BLE001
                log.warning("生成 %s 失败: %s", target, e)


def _read_port():
    """从 config.yaml 读取 server.port（yaml 失败时正则兜底）。"""
    cfg_path = os.path.join(APP_DIR, "config.yaml")
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


_ensure_config()
PORT = _read_port()
BASE_URL = "http://127.0.0.1:%d" % PORT
HEALTH_URL = BASE_URL + "/api/health"
START_TIMEOUT = 180          # 后端就绪最长等待（秒）

# 本地健康检查强制绕过系统代理（避免代理干扰 localhost 探测）
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_backend_proc = None
_closed = False


def log_tail(n=30):
    """读取桌面日志末尾 n 行，用于错误页展示。"""
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-n:])
    except Exception:  # noqa: BLE001
        return ""


def port_in_use(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex(("127.0.0.1", port)) == 0
    except Exception:  # noqa: BLE001
        return False


def health_status():
    """返回 /api/health 的 JSON 解析结果；失败返回 None。"""
    try:
        import json
        with _OPENER.open(HEALTH_URL, timeout=2) as r:
            if r.status == 200:
                return json.loads(r.read().decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        pass
    return None


def health_ok():
    try:
        with _OPENER.open(HEALTH_URL, timeout=2) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def _backend_cmd():
    """后端子进程命令：打包=exe --backend；源码=python desktop_app.py --backend。"""
    if FROZEN:
        return [sys.executable, "--backend"]
    return [sys.executable, os.path.abspath(__file__), "--backend"]


def start_backend():
    """启动后端子进程；若服务已在运行（含正在启动中）则等待就绪后复用。"""
    global _backend_proc
    if port_in_use(PORT):
        log.info("端口 %d 已有进程监听，等待服务就绪以便复用...", PORT)
        for _ in range(40):
            if health_ok():
                log.info("检测到服务已在运行 (端口 %s)，直接复用", PORT)
                return
            time.sleep(0.5)
        raise RuntimeError("端口 %d 已被其他程序占用，无法启动本系统服务" % PORT)
    log.info("启动后端子进程: %s", _backend_cmd())
    _backend_log = open(os.path.join(APP_DIR, "data", "backend.log"), "ab", buffering=0)
    _backend_env = dict(os.environ)
    _backend_env["PYTHONIOENCODING"] = "utf-8"
    _backend_proc = subprocess.Popen(
        _backend_cmd(),
        cwd=APP_DIR,
        env=_backend_env,
        stdout=_backend_log,
        stderr=subprocess.STDOUT,
    )
    log.info("后端子进程已启动 (PID=%s)", _backend_proc.pid)


def stop_backend():
    """终止本进程启动的后端子进程（复用场景下不动作）。"""
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


def _patch_window_icon():
    """在 WinForms 窗口创建时即设置 Form.Icon（任务栏按钮生成前生效）。"""
    try:
        from webview.platforms import winforms
        orig_init = winforms.BrowserView.BrowserForm.__init__

        def patched_init(self, window, cache_dir):
            orig_init(self, window, cache_dir)
            try:
                ico_path = os.path.join(BUNDLE_DIR, "oa_agent.ico")
                if os.path.exists(ico_path):
                    import clr
                    clr.AddReference("System.Drawing")
                    from System.Drawing import Icon
                    self.Icon = Icon(ico_path)
                    log.info("窗口创建时已设置图标: %s", ico_path)
            except Exception as e:  # noqa: BLE001
                log.warning("窗口创建时设置图标失败: %s", e)

        winforms.BrowserView.BrowserForm.__init__ = patched_init
        log.info("窗口图标补丁已安装")
    except Exception as e:  # noqa: BLE001
        log.warning("窗口图标补丁安装失败: %s", e)


def _set_window_icon(window):
    """设置窗口标题栏与任务栏图标（Win32 WM_SETICON，双保险）。"""
    try:
        import ctypes
        native = getattr(window, "native", None)
        if native is None:
            log.warning("窗口原生对象不可用，跳过图标设置")
            return
        ico_path = os.path.join(BUNDLE_DIR, "oa_agent.ico")
        if not os.path.exists(ico_path):
            log.warning("图标文件不存在: %s", ico_path)
            return
        hwnd = native.Handle.ToInt32()
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x00000010
        hicon = ctypes.windll.user32.LoadImageW(
            None, ico_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE
        )
        if not hicon:
            log.warning("加载图标失败: %s", ico_path)
            return
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1
        ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon)    # 任务栏/Alt+Tab
        ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon)  # 标题栏
        log.info("窗口图标已设置 (hwnd=%s)", hwnd)
    except Exception as e:  # noqa: BLE001
        log.warning("设置窗口图标失败: %s", e)


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
  <button id="skip" style="margin-top:26px;padding:8px 22px;border:1px solid #3b4156;border-radius:8px;background:transparent;color:#a5a9bd;font-size:13px;cursor:pointer;display:none;">跳过等待，直接进入</button>
  <script>
  setTimeout(function(){ document.getElementById('skip').style.display='block'; }, 8000);
  document.getElementById('skip').addEventListener('click', function(){ window._skipWait = true; });
  </script>
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


def _wait_set_window_icon(window):
    """等待窗口原生对象就绪后设置图标（最多 20s）。"""
    for _ in range(40):
        if getattr(window, "native", None) is not None:
            break
        time.sleep(0.5)
    _set_window_icon(window)


def bootstrap(window):
    """webview.start 启动后在线程中运行：拉起后端并等待就绪。"""
    _wait_set_window_icon(window)
    if FROZEN and not os.path.isdir(EXTERNAL_APP_DIR):
        log.error("缺少外置代码目录 app/（应位于 exe 同目录）")
        window.load_html(error_html(
            "缺少外置代码目录 app/（应位于 exe 同目录）。\n\n"
            "请重新解压/安装完整的 OA运维Agent 目录（不要只拷贝单个 exe 文件）。"
        ))
        return
    t0 = time.time()
    try:
        start_backend()
    except Exception as e:  # noqa: BLE001
        log.error("启动失败: %s", e)
        window.load_html(error_html("启动失败：%s\n\n请查看 data/desktop.log" % e))
        return

    while not _closed and time.time() - t0 < START_TIMEOUT:
        try:
            if window.evaluate_js("window._skipWait === true"):
                log.info("用户选择跳过等待，载入主界面（引擎后台继续预热）")
                window.load_url(BASE_URL + "/")
                return
        except Exception:  # noqa: BLE001
            pass
        st = health_status()
        if st and st.get("kb_state") in ("ready", "unavailable"):
            log.info("服务就绪 (kb_state=%s, %.1fs)，载入主界面", st.get("kb_state"), time.time() - t0)
            window.load_url(BASE_URL + "/")
            return
        waited = int(time.time() - t0)
        # 启动画面实时状态：模型加载中 / 等待服务启动
        if st and st.get("kb_state") == "loading":
            detail = "正在加载本地嵌入模型…（首次启动约 20~40 秒）"
        else:
            detail = "正在等待后端服务启动…"
        try:
            js = ("document.getElementById('status').textContent = '正在启动服务… 已等待 %d 秒';"
                  "document.getElementById('detail').textContent = '%s';" % (waited, detail))
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

    _patch_window_icon()  # 窗口创建时即设置任务栏图标

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
            storage_path=os.path.join(APP_DIR, "data", "webview_profile"),
        )
    except Exception as e:  # noqa: BLE001
        log.error("webview 启动异常: %s", e)
        import webbrowser
        webbrowser.open(BASE_URL + "/")
    finally:
        stop_backend()
        log.info("桌面版已退出")


def run_backend_server():
    """后端子进程模式：仅运行 uvicorn 服务（无 GUI）。"""
    log.info("后端模式启动 (port=%d)...", PORT)
    if FROZEN and not os.path.isdir(EXTERNAL_APP_DIR):
        log.error("缺少外置代码目录 app/（应位于 exe 同目录），请勿删除或移动该文件夹")
        sys.exit(1)
    import uvicorn
    from ui.server import app as fastapi_app
    uvicorn.run(fastapi_app, host="127.0.0.1", port=PORT, log_level="info")


if __name__ == "__main__":
    if "--backend" in sys.argv:
        try:
            run_backend_server()
        except Exception as e:  # noqa: BLE001
            log.error("后端进程异常退出: %s", e)
            raise
    else:
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