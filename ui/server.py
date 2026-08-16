"""
OA运维助手 — FastAPI Web 服务端
================================
纯 HTML/CSS/JS 前端 + FastAPI 后端。
所有 agent 模块代码完全不动，通过 API 端点调用。
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn

from agents.inspection_agent import InspectionAgent, run_unified_inspection
from agents.log_analysis_agent import LogAnalysisAgent, analyze_log_content
from agents.knowledge_agent import KnowledgeBaseAgent
from agents.ssl_monitor import check_cert_expiry, batch_check_certs
from agents.network_diag import ping_host, check_tcp_port, dns_resolve, traceroute_host, http_health_check
from agents.db_inspector import check_mysql_status, check_redis_status, show_mysql_slow_queries, check_mssql_status, check_oracle_status
from agents.security_audit import audit_ssh_config, check_failed_logins, audit_firewall_rules, check_listening_ports, audit_cron_jobs
from utils.logger import get_logger
from utils.config import config as app_config
from utils.database import db
from utils.scheduler import InspectionScheduler
from utils.dashboard import dashboard_manager

logger = get_logger(__name__)

# ---- FastAPI App ----
from contextlib import asynccontextmanager


def _ensure_ollama_running() -> bool:
    """provider=ollama 时检测本地 Ollama，未运行则自动拉起（与 scripts/启动.bat 第3步一致）。

    返回是否就绪；任何失败只告警不抛出（无 Ollama 的机器优雅降级，
    问答走既有 Connection error 回退逻辑）。
    """
    import time
    import shutil as _shutil
    import subprocess as _subprocess
    import urllib.request as _urlreq

    base = app_config.get("llm.ollama.base_url", "http://localhost:11434/v1")
    version_url = base.rstrip("/v1").rstrip("/") + "/api/version"
    # 强制绕过系统代理（历史坑：代理会干扰 localhost 探测）
    _opener = _urlreq.build_opener(_urlreq.ProxyHandler({}))

    def _reachable() -> bool:
        try:
            with _opener.open(version_url, timeout=2) as r:
                return r.status == 200
        except Exception:  # noqa: BLE001
            return False

    if _reachable():
        logger.info("Ollama 服务已在运行 (%s)", base)
        return True

    exe = _shutil.which("ollama")
    if exe is None:
        for _p in (
            r"E:\Ollama\ollama.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe"),
            r"C:\Program Files\Ollama\ollama.exe",
        ):
            if os.path.isfile(_p):
                exe = _p
                break
    if exe is None:
        logger.warning(
            "provider=ollama 但 Ollama 未运行且找不到 ollama.exe（11434 无响应），"
            "本地 LLM 不可用；如需使用请安装并启动 Ollama"
        )
        return False

    logger.info("Ollama 未运行，自动启动: %s serve", exe)
    try:
        flags = 0
        if sys.platform == "win32":
            flags = _subprocess.DETACHED_PROCESS | _subprocess.CREATE_NO_WINDOW
        _subprocess.Popen(
            [exe, "serve"],
            stdout=_subprocess.DEVNULL,
            stderr=_subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Ollama 自动启动失败: %s", e)
        return False

    for _ in range(40):  # 最多等 20 秒
        time.sleep(0.5)
        if _reachable():
            logger.info("Ollama 已就绪（自动启动成功）")
            return True
    logger.warning("Ollama 启动超时（20s），如持续失败请手动运行 ollama serve")
    return False


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """服务启动时：后台拉起 Ollama（如配置）+ 预热知识库引擎（均不阻塞启动）。"""
    if app_config.get("llm.provider", "ollama") == "ollama":
        _threading.Thread(target=_ensure_ollama_running, daemon=True, name="ollama-ensure").start()
    _prewarm_kb()
    yield


app = FastAPI(title="OA 运维助手", version="2.5.0", lifespan=_lifespan)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# ---- 全局状态 ----
scheduler = InspectionScheduler()

# ============ 页面路由 ============

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    mode = app_config.get("inspection.mode", "simulated")
    mode_label = {"ssh": "SSH远程", "local": "本机", "auto": "SSH优先", "simulated": "模拟"}.get(mode, mode)
    sched_status = f"调度器: {'运行中' if scheduler.is_running else '已停止'} | 模式: {mode_label}"
    import time
    template = templates.get_template("index.html")
    html = template.render(
        version="2.5.0",
        cache_buster=str(int(time.time())),
        scheduler_status=sched_status,
        is_running=scheduler.is_running,
    )
    return HTMLResponse(html)



# ============ 健康检查（桌面版启动画面轮询用） ============

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "version": "2.5.0",
        "kb_state": _kb_state.get("state", "loading"),
        "kb_error": _kb_state.get("error"),
    }


# ============ 开发热刷新（仅 ?dev=1 打开的页面会轮询，生产零影响） ============

@app.get("/api/dev/version")
async def dev_version():
    """返回 ui/ 前端文件（templates/static）最新修改时间戳。

    开发模式：浏览器以 ?dev=1 打开首页后，前端每 2s 轮询此端点，
    时间戳变化即自动刷新页面，改 HTML/CSS/JS 无需手动刷新或重启服务。
    """
    latest = 0.0
    for sub in ("templates", "static"):
        root = os.path.join(BASE_DIR, sub)
        if not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                try:
                    latest = max(latest, os.path.getmtime(os.path.join(dirpath, fn)))
                except OSError:
                    pass
    return {"version": f"{latest:.3f}", "ts": latest}

# ============ 巡检 API ============

def _inspect_with_dashboard():
    """执行巡检并将结果推送到仪表盘（调度器回调 + 手动巡检共用）"""
    result = run_unified_inspection()
    dashboard_manager.push(result)
    return result


@app.post("/api/inspect/run")
async def api_inspect():
    result = _inspect_with_dashboard()
    return {"result": result}


@app.post("/api/inspect/start")
async def api_inspect_start(interval: int = Form(...)):
    ok = scheduler.start(task_func=_inspect_with_dashboard, interval=interval)
    return {"ok": ok, "msg": "已启动" if ok else "已在运行中"}

@app.post("/api/inspect/stop")
async def api_inspect_stop():
    ok = scheduler.stop()
    mode = app_config.get("inspection.mode", "simulated")
    mode_label = {"ssh": "SSH远程", "local": "本机", "auto": "SSH优先", "simulated": "模拟"}.get(mode, mode)
    return {
        "ok": ok, "msg": "已停止" if ok else "未运行",
        "status": f"调度器: {'运行中' if scheduler.is_running else '已停止'} | 模式: {mode_label} | 间隔: {scheduler.interval}秒"
    }

@app.post("/api/inspect/adjust")
async def api_inspect_adjust(interval: int = Form(...)):
    ok = scheduler.adjust_interval(interval)
    return {"ok": ok, "msg": f"间隔已调整: {interval}秒" if ok else "巡检未运行"}

@app.get("/api/inspect/status")
async def api_inspect_status():
    mode = app_config.get("inspection.mode", "simulated")
    mode_label = {"ssh": "SSH远程", "local": "本机", "auto": "SSH优先", "simulated": "模拟"}.get(mode, mode)
    return {
        "running": scheduler.is_running,
        "interval": scheduler.interval,
        "mode": mode_label,
        "status": f"调度器: {'运行中' if scheduler.is_running else '已停止'} | 模式: {mode_label} | 间隔: {scheduler.interval}秒"
    }

@app.get("/api/inspect/history")
async def api_inspect_history(days: int = 7):
    try:
        summary = db.get_inspection_summary(days)
        records = db.get_inspection_history(days=days, limit=20)
        return {"summary": summary, "records": records}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/db/overview")
async def api_db_overview():
    try:
        return db.get_db_stats()
    except Exception as e:
        return {"error": str(e)}


# ============ 日志分析 API ============

@app.post("/api/log/analyze")
async def api_log_analyze(log_text: str = Form("")):
    if not log_text.strip():
        return {"result": "请输入需要分析的日志内容"}
    result = analyze_log_content.invoke({"log_text": log_text})
    return {"result": result}


@app.post("/api/log/ocr")
async def api_log_ocr(file: UploadFile = File(...)):
    """上传截图 → OCR 识别 → 自动分析"""
    from utils.ocr import extract_text_from_bytes

    content = await file.read()
    try:
        ocr_text = extract_text_from_bytes(content)
    except Exception as e:
        return {"text": "", "result": f"OCR 识别失败: {str(e)}"}

    if not ocr_text.strip():
        return {"text": "", "result": "OCR 未能识别到文字，请确认图片清晰度并重试"}

    # 将识别结果送入日志分析
    analysis = analyze_log_content.invoke({"log_text": ocr_text})
    return {"text": ocr_text, "result": analysis}


# ============ SSL 证书 API ============

@app.post("/api/ssl/check")
async def api_ssl_check(domain: str = Form(...)):
    result = check_cert_expiry.invoke({"domain": domain})
    return {"result": result}

@app.post("/api/ssl/batch")
async def api_ssl_batch(domains: str = Form(...)):
    result = batch_check_certs.invoke({"domains_text": domains})
    return {"result": result}


# ============ 网络诊断 API ============

@app.post("/api/net/ping")
async def api_ping(host: str = Form(...)):
    return {"result": ping_host.invoke({"host": host})}

@app.post("/api/net/port")
async def api_port(host_port: str = Form(...)):
    return {"result": check_tcp_port.invoke({"host_port": host_port})}

@app.post("/api/net/dns")
async def api_dns(domain: str = Form(...)):
    return {"result": dns_resolve.invoke({"domain": domain})}

@app.post("/api/net/trace")
async def api_trace(host: str = Form(...)):
    return {"result": traceroute_host.invoke({"host": host})}

@app.post("/api/net/http")
async def api_http(url: str = Form(...)):
    return {"result": http_health_check.invoke({"url": url})}


# ============ 数据库 API ============

@app.post("/api/db/mysql")
async def api_mysql(host: str = Form("127.0.0.1"), port: int = Form(3306),
                    user: str = Form("root"), password: str = Form("")):
    config_text = f"host={host} port={port} user={user} password={password}"
    return {"result": check_mysql_status.invoke({"config_text": config_text})}

@app.post("/api/db/mysql/slow")
async def api_mysql_slow(host: str = Form("127.0.0.1"), port: int = Form(3306),
                         user: str = Form("root"), password: str = Form("")):
    config_text = f"host={host} port={port} user={user} password={password} limit=20"
    return {"result": show_mysql_slow_queries.invoke({"config_text": config_text})}

@app.post("/api/db/redis")
async def api_redis(host: str = Form("127.0.0.1"), port: int = Form(6379), password: str = Form("")):
    config_text = f"host={host} port={port} password={password}"
    return {"result": check_redis_status.invoke({"config_text": config_text})}

@app.post("/api/db/mssql")
async def api_mssql(host: str = Form("127.0.0.1"), port: int = Form(1433),
                    user: str = Form("sa"), password: str = Form("")):
    config_text = f"host={host} port={port} user={user} password={password}"
    return {"result": check_mssql_status.invoke({"config_text": config_text})}

@app.post("/api/db/oracle")
async def api_oracle(host: str = Form("127.0.0.1"), port: int = Form(1521),
                     user: str = Form("system"), password: str = Form(""), service: str = Form("orcl")):
    config_text = f"host={host} port={port} user={user} password={password} service={service}"
    return {"result": check_oracle_status.invoke({"config_text": config_text})}


# ============ 安全基线 API ============

@app.post("/api/sec/ssh")
async def api_sec_ssh(): return {"result": audit_ssh_config.invoke({})}
@app.post("/api/sec/login")
async def api_sec_login(): return {"result": check_failed_logins.invoke({})}
@app.post("/api/sec/firewall")
async def api_sec_fw(): return {"result": audit_firewall_rules.invoke({})}
@app.post("/api/sec/ports")
async def api_sec_ports(): return {"result": check_listening_ports.invoke({})}
@app.post("/api/sec/cron")
async def api_sec_cron(): return {"result": audit_cron_jobs.invoke({})}
@app.post("/api/sec/all")
async def api_sec_all():
    result = (
        "=" * 55 + "\n  全量安全基线审计报告\n" + "=" * 55 + "\n\n" +
        audit_ssh_config.invoke({}) + "\n\n" + check_failed_logins.invoke({}) + "\n\n" +
        audit_firewall_rules.invoke({}) + "\n\n" + check_listening_ports.invoke({}) + "\n\n" +
        audit_cron_jobs.invoke({})
    )
    return {"result": result}


# ============ 实时监控仪表盘 API ============

@app.get("/api/dashboard/metrics")
async def api_dashboard_metrics():
    """返回最新一次巡检的结构化指标，仪表盘首次加载和手动刷新时调用"""
    metrics = dashboard_manager.get_latest()
    if metrics is None:
        return {"timestamp": None, "summary": None, "checks": [], "alerts": [], "mode": None}
    return metrics


@app.get("/api/dashboard/stream")
async def api_dashboard_stream():
    """
    SSE 实时推送端点。
    浏览器通过 EventSource 连接，每次巡检完成后自动收到结构化指标。
    每 30 秒发送一次心跳保持连接。
    """
    import asyncio
    import json as _json

    async def event_generator():
        q = dashboard_manager.subscribe()
        try:
            while True:
                try:
                    data = await asyncio.wait_for(q.get(), timeout=30)
                    yield f"data: {_json.dumps(data, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    # 心跳保活
                    yield ": heartbeat\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            dashboard_manager.unsubscribe(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 Nginx 缓冲
        },
    )


@app.get("/api/dashboard/history")
async def api_dashboard_history(minutes: int = 60):
    """返回最近 N 分钟的时间序列数据，供趋势图表使用"""
    timeline = dashboard_manager.get_history(minutes)
    return {"timeline": timeline}


def _save_api_key_to_env(api_key: str) -> bool:
    """把 API Key 写入 .env 文件（不回写 config.yaml 明文），并注入当前进程环境。"""
    try:
        from utils.config import PROJECT_ROOT
        env_file = os.path.join(PROJECT_ROOT, ".env")
        lines = []
        if os.path.exists(env_file):
            with open(env_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
        found = False
        for i, line in enumerate(lines):
            if line.strip().startswith("OA_LLM_API_KEY="):
                lines[i] = f"OA_LLM_API_KEY={api_key}\n"
                found = True
                break
        if not found:
            lines.append(f"OA_LLM_API_KEY={api_key}\n")
        with open(env_file, "w", encoding="utf-8") as f:
            f.writelines(lines)
        os.environ["OA_LLM_API_KEY"] = api_key  # 热切换立即生效
        logger.info("API Key 已写入 .env（config.yaml 保持占位符）")
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("API Key 写入 .env 失败: %s", e)
        return False

# ============ 系统配置 API ============

@app.post("/api/config/save")
async def api_config_save(
    provider: str = Form(...),
    api_key: str = Form(""),
    base_url: str = Form(""),
    model: str = Form(""),
):
    """
    保存 LLM 配置到 config.yaml 并热切换。
    provider: ollama / deepseek
    """
    valid_providers = {"ollama", "deepseek", "qwen", "openai"}
    if provider not in valid_providers:
        return {"ok": False, "error": f"无效的 provider: {provider}，支持: {', '.join(valid_providers)}"}

    # 构建要更新的配置项
    updates = {"llm.provider": provider}

    if provider == "ollama":
        updates["llm.ollama.model"] = model or "qwen3:8b"
        updates["llm.ollama.base_url"] = base_url or "http://localhost:11434/v1"
        updates["llm.ollama.api_key"] = api_key or "ollama"
    else:
        updates["llm.model"] = model or "deepseek-chat"
        updates["llm.base_url"] = base_url or "https://api.deepseek.com/v1"
        if api_key:
            # 安全：明文 Key 只写 .env，config.yaml 保持 ${OA_LLM_API_KEY:} 占位符
            if not _save_api_key_to_env(api_key):
                return {"ok": False, "error": "API Key 写入 .env 失败，未保存配置"}

    # 持久化到 config.yaml
    if not app_config.update_file(updates):
        return {"ok": False, "error": "配置文件写入失败，请检查 config.yaml 是否被占用"}

    # 热切换：重置知识库引擎状态，后台按新配置重新预热
    global _kb_agent, _kb_generation, _kb_init_generation
    with _kb_lock:
        _kb_agent = None
        _kb_generation += 1              # 使进行中的旧预热线程结果作废
        _kb_init_generation = None
        _kb_state.update(state="loading", error=None)
    _prewarm_kb()

    logger.info(f"LLM 配置已热切换: provider={provider}, model={model or updates.get('llm.model', updates.get('llm.ollama.model', ''))}")
    return {
        "ok": True,
        "provider": provider,
        "model": model or (updates.get("llm.model") or updates.get("llm.ollama.model", "")),
    }


# ============ 知识库 API（需 LLM 初始化后可用）============

# 知识库 Agent 需要嵌入模型（首次加载约 20~40 秒）。
# 启动时后台预热 + 线程安全懒加载 + 就绪状态机。
import threading as _threading

_kb_agent = None
_kb_lock = _threading.Lock()
_kb_generation = 0            # 热切换代数：用于丢弃按旧配置构建的过期实例
_kb_init_generation = None    # 正在初始化的代数；None = 空闲
_kb_state = {"state": "loading", "error": None}   # loading | ready | unavailable


def _build_kb_agent():
    """按 provider 路由构建 KnowledgeBaseAgent；未配置时返回 (None, 原因)。"""
    provider = app_config.get("llm.provider", "ollama")
    if provider == "ollama":
        api_key = "ollama"
        base_url = app_config.get("llm.ollama.base_url", "http://localhost:11434/v1")
        model = app_config.get("llm.ollama.model", "qwen3:8b")
    else:
        api_key = app_config.get("llm.api_key", "")
        base_url = app_config.get("llm.base_url", "")
        model = app_config.get("llm.model", "")
        if not api_key:
            return None, "未配置 LLM API Key（请在 .env 中设置 OA_LLM_API_KEY）"
    kb = KnowledgeBaseAgent(llm_api_key=api_key, llm_base_url=base_url, llm_model=model)
    return kb, None


def get_kb_agent():
    """线程安全获取 KB Agent（多线程同时请求只会初始化一次）。

    返回 None 的三种情形：
      - 另一线程正在初始化（state=loading，端点应返回"初始化中"提示）
      - 初始化失败 / 模型不可用（state=unavailable）
      - LLM 未配置（state=unavailable）
    """
    global _kb_agent, _kb_init_generation
    if _kb_agent is not None:
        return _kb_agent
    with _kb_lock:
        if _kb_agent is not None:            # double-check
            return _kb_agent
        if _kb_state.get("state") == "unavailable":
            # 粘性不可用：初始化失败后不再随请求重试（避免每次请求都阻塞 20~40s），
            # 仅通过配置热切换或重启应用恢复
            return None
        if _kb_init_generation is not None:  # 已有线程在初始化（20~40s）
            return None
        _kb_init_generation = _kb_generation
        _kb_state.update(state="loading", error=None)
        gen = _kb_generation
    kb = None
    try:
        kb, err = _build_kb_agent()
        with _kb_lock:
            if gen == _kb_generation:
                if kb is None:
                    _kb_state.update(state="unavailable", error=err or "知识库引擎初始化失败")
                else:
                    _kb_agent = kb
                    _kb_state.update(state="ready", error=None)
            else:
                # 热切换已重置：丢弃按旧配置构建的实例，释放资源
                if kb is not None:
                    try:
                        kb.close()
                    except Exception:  # noqa: BLE001
                        pass
                kb = None
    except Exception as e:
        logger.error("知识库Agent初始化失败: %s", e)
        with _kb_lock:
            if gen == _kb_generation:
                _kb_state.update(state="unavailable", error=str(e))
        return None
    finally:
        with _kb_lock:
            if _kb_init_generation == gen:
                _kb_init_generation = None
    if kb is None or gen != _kb_generation:
        return None
    logger.info("知识库引擎初始化完成（ready）")
    return kb


def _prewarm_kb():
    """后台线程预热 KB Agent（不阻塞服务启动）。"""
    _threading.Thread(target=get_kb_agent, daemon=True, name="kb-prewarm").start()


def _kb_not_ready():
    """KB 未就绪时的统一状态与提示文案。返回 (state, msg)。"""
    if _kb_state.get("state") == "unavailable":
        return "unavailable", "知识库引擎不可用：" + (_kb_state.get("error") or "请检查 LLM 配置")
    return "loading", "知识库引擎正在初始化（首次加载嵌入模型约 20~40 秒），请稍候…"

@app.post("/api/kb/ask")
async def api_kb_ask(question: str = Form(...)):
    kb = get_kb_agent()
    if kb is None:
        state, msg = _kb_not_ready()
        return {"state": state, "result": msg}
    return {"result": kb.query(question)}

@app.post("/api/kb/import")
async def api_kb_import(file: UploadFile = File(...)):
    kb = get_kb_agent()
    if kb is None:
        state, msg = _kb_not_ready()
        return {"state": state, "result": msg}
    # 保存上传文件到临时目录（文件名净化，防止路径穿越）
    os.makedirs("data/uploads", exist_ok=True)
    safe_name = os.path.basename(file.filename or "upload.bin")
    file_path = os.path.join("data/uploads", safe_name)
    with open(file_path, "wb") as f:
        content = await file.read()
        f.write(content)
    # 导入知识库
    result = kb.import_document(file_path)
    # 清理临时文件
    try:
        os.remove(file_path)
    except Exception:
        pass
    return {"result": result}

@app.get("/api/kb/list")
async def api_kb_list():
    kb = get_kb_agent()
    if kb is None:
        state, msg = _kb_not_ready()
        return {"state": state, "result": msg}
    return {"result": kb.list_documents()}

@app.get("/api/kb/stats")
async def api_kb_stats():
    kb = get_kb_agent()
    if kb is None:
        state, msg = _kb_not_ready()
        return {"state": state, "result": msg}
    return {"result": kb.get_stats()}

@app.get("/api/kb/document/{doc_name}")
async def api_kb_document(doc_name: str):
    """获取指定文档的完整内容。"""
    kb = get_kb_agent()
    if kb is None:
        state, msg = _kb_not_ready()
        return {"state": state, "result": msg}
    # URL 解码
    from urllib.parse import unquote
    return {"result": kb.get_document_text(unquote(doc_name))}

@app.post("/api/kb/delete")
async def api_kb_delete(doc_name: str = Form(...)):
    kb = get_kb_agent()
    if kb is None:
        state, msg = _kb_not_ready()
        return {"state": state, "result": msg}
    return {"result": kb.delete_document(doc_name)}

@app.post("/api/kb/clear")
async def api_kb_clear():
    kb = get_kb_agent()
    if kb is None:
        state, msg = _kb_not_ready()
        return {"state": state, "result": msg}
    return {"result": kb.clear_knowledge_base()}


# ============ 对话 Chat API (v2.4) ============

@app.post("/api/kb/chat")
async def api_kb_chat(
    message: str = Form(...),
    conversation_id: str = Form("default"),
):
    """
    多轮对话问答（带记忆）。

    Args:
        message: 用户消息
        conversation_id: 对话 ID，不同 ID 独立记忆。默认 "default"
    """
    kb = get_kb_agent()
    if kb is None:
        state, msg = _kb_not_ready()
        return {"answer": msg, "state": state, "conversation_id": conversation_id}
    answer = kb.chat(message, conversation_id=conversation_id)
    return {"answer": answer, "conversation_id": conversation_id}

@app.post("/api/kb/chat/stream")
async def api_kb_chat_stream(
    message: str = Form(...),
    conversation_id: str = Form("default"),
):
    """SSE 流式问答：逐 token 推送最终回答。"""
    import json as _json

    kb = get_kb_agent()

    async def event_gen():
        if kb is None:
            state, msg = _kb_not_ready()
            yield f"data: {_json.dumps({'error': msg, 'state': state}, ensure_ascii=False)}\n\n"
            return
        try:
            gen = kb.chat(message, conversation_id=conversation_id, stream=True)
            for chunk in gen:
                yield f"data: {_json.dumps({'delta': chunk}, ensure_ascii=False)}\n\n"
            yield f"data: {_json.dumps({'done': True, 'conversation_id': conversation_id}, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.warning(f"流式问答异常: {e}")
            yield f"data: {_json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.post("/api/kb/chat/clear")
async def api_kb_chat_clear(conversation_id: str = Form("default")):
    """清除指定对话的历史记录。"""
    kb = get_kb_agent()
    if kb is None:
        return {"ok": False}
    kb.clear_conversation(conversation_id)
    return {"ok": True, "conversation_id": conversation_id}


@app.get("/api/kb/chat/history")
async def api_kb_chat_history(conversation_id: str = "default"):
    """获取对话历史。"""
    rows = db.get_conversation_messages(conversation_id)
    return {"messages": [{"role": r["role"], "content": r["content"]} for r in rows]}

@app.get("/api/kb/conversations")
async def api_kb_conversations(limit: int = 50):
    """对话列表（按最近更新倒序）。"""
    return {"conversations": db.list_conversations(limit)}


@app.post("/api/kb/conversation")
async def api_kb_conversation_create():
    """新建对话，返回新对话 ID。"""
    conv_id = db.create_conversation("新对话")
    return {"ok": True, "conversation_id": conv_id, "title": "新对话"}


@app.post("/api/kb/conversation/delete")
async def api_kb_conversation_delete(conversation_id: str = Form(...)):
    """删除对话及其全部消息。"""
    ok = db.delete_conversation(conversation_id)
    return {"ok": ok, "conversation_id": conversation_id}


@app.post("/api/kb/conversation/rename")
async def api_kb_conversation_rename(
    conversation_id: str = Form(...),
    title: str = Form(...),
):
    """重命名对话标题。"""
    ok = db.rename_conversation(conversation_id, title.strip() or "新对话")
    return {"ok": ok, "conversation_id": conversation_id, "title": title}


# ============ 批量问答 API (v2.4) ============

@app.post("/api/kb/batch-ask")
async def api_kb_batch_ask(questions: str = Form("")):
    """
    批量处理多个问题（最多 20 题并行）。

    Args:
        questions: 换行分隔的问题列表
    """
    kb = get_kb_agent()
    if kb is None:
        state, msg = _kb_not_ready()
        return {"results": [], "error": msg, "state": state}

    question_list = [q.strip() for q in questions.split("\n") if q.strip()]
    if not question_list:
        return {"results": [], "error": "未提供有效问题"}

    max_batch = app_config.get("knowledge_base.agentic_rag.batch_max_questions", 20)
    if len(question_list) > max_batch:
        question_list = question_list[:max_batch]

    import concurrent.futures

    provider = app_config.get("llm.provider", "ollama")
    max_workers = min(len(question_list), app_config.get("knowledge_base.agentic_rag.parallel_workers", 5))

    logger.info(f"批量问答: {len(question_list)} 题, workers={max_workers}")

    results = [None] * len(question_list)

    def process(idx, q):
        try:
            return idx, kb.query(q)
        except Exception as e:
            return idx, f"[错误] {str(e)}"

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process, i, q): i for i, q in enumerate(question_list)}
        for future in concurrent.futures.as_completed(futures):
            try:
                idx, answer = future.result()
                results[idx] = {"question": question_list[idx], "answer": answer}
            except Exception as e:
                idx = futures[future]
                results[idx] = {"question": question_list[idx], "answer": f"[错误] {e}"}

    return {"results": results, "total": len(results)}


# ============ 知识图谱 API (v2.4) ============

def _get_kg_store():
    """获取知识图谱存储实例（从 KB Agent 中提取）。"""
    kb = get_kb_agent()
    if kb is None:
        return None
    if not hasattr(kb, "kg_store") or kb.kg_store is None:
        return None
    if kb.kg_store.graph.number_of_nodes() == 0:
        return None
    return kb.kg_store


@app.post("/api/kg/search")
async def api_kg_search(query: str = Form(...), entity_type: str = Form(None)):
    """搜索知识图谱中的实体（按名称/描述）。"""
    store = _get_kg_store()
    if store is None:
        return JSONResponse(
            {"error": "知识图谱不可用。请先导入文档以构建图谱。"},
            status_code=503,
        )
    etype = entity_type if entity_type and entity_type != "all" else None
    results = store.search_nodes(query, entity_type=etype, limit=30)
    return {
        "results": results,
        "total": len(results),
        "stats": store.get_stats(),
    }


@app.post("/api/kg/explore")
async def api_kg_explore(node_id: str = Form(...), depth: int = Form(2)):
    """提取以指定节点为中心的子图（供可视化）。"""
    store = _get_kg_store()
    if store is None:
        return JSONResponse(
            {"error": "知识图谱不可用"},
            status_code=503,
        )
    subgraph = store.extract_subgraph(node_id, depth=min(depth, 5))
    return {"subgraph": subgraph}


@app.post("/api/kg/path")
async def api_kg_path(source: str = Form(...), target: str = Form(...)):
    """查找两个实体之间的最短路径。"""
    store = _get_kg_store()
    if store is None:
        return JSONResponse(
            {"error": "知识图谱不可用"},
            status_code=503,
        )
    result = store.find_shortest_path(source, target)
    if result.get("error"):
        return {"error": result["error"], "path": [], "length": -1}
    return {"path": result["path"], "length": result["length"], "nodes": result.get("nodes", []), "edges": result.get("edges", [])}


@app.get("/api/kg/stats")
async def api_kg_stats():
    """获取知识图谱统计信息。"""
    store = _get_kg_store()
    if store is None:
        return {
            "available": False,
            "message": "知识图谱不可用。请先导入文档或启用 kg.enabled。",
        }
    stats = store.get_stats()
    stats["available"] = True
    return stats


# ============ 运维命令大全 API ============

@app.get("/api/commands")
async def api_commands():
    """
    返回运维常用命令大全数据。
    前端优先使用静态 commands.js 加载，此端点作为备用/扩展接口。
    数据来源: ui/static/commands.js 中的 OPS_COMMANDS 对象。
    """
    import json as _json
    import re

    commands_path = os.path.join(BASE_DIR, "static", "commands.js")
    try:
        with open(commands_path, "r", encoding="utf-8") as f:
            content = f.read()
        # 提取 JS 对象: OPS_COMMANDS = { ... };
        start = content.index("{")
        end = content.rindex("}") + 1
        js_obj = content[start:end]
        # 移除 JS 注释 (只移除行首 // 注释,避免误删 URL 中的 //)
        js_obj = re.sub(r'^\s*//.*$', '', js_obj, flags=re.MULTILINE)
        js_obj = re.sub(r'/\*.*?\*/', '', js_obj, flags=re.DOTALL)
        # 将 JS 对象键名加引号以符合 JSON 规范 (word: → "word":)
        js_obj = re.sub(r'([\{,]\s*)(\w+)\s*:', r'\1"\2":', js_obj)
        data = _json.loads(js_obj)
        return data
    except Exception as e:
        logger.error(f"加载命令数据失败: {e}")
        return {"categories": [], "commands": [], "error": str(e)}


# ============ 启动入口 ============

def run_server(host: str = "127.0.0.1", port: int = 7860):
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run_server()
