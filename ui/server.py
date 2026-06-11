"""
OA运维助手 — FastAPI Web 服务端
================================
纯 HTML/CSS/JS 前端 + FastAPI 后端。
所有 agent 模块代码完全不动，通过 API 端点调用。
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
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

logger = get_logger(__name__)

# ---- FastAPI App ----
app = FastAPI(title="OA 运维助手", version="2.2")

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
        version="2.2",
        cache_buster=str(int(time.time())),
        scheduler_status=sched_status,
        is_running=scheduler.is_running,
    )
    return HTMLResponse(html)


# ============ 巡检 API ============

@app.post("/api/inspect/run")
async def api_inspect():
    result = run_unified_inspection()
    return {"result": result}

@app.post("/api/inspect/start")
async def api_inspect_start(interval: int = Form(...)):
    ok = scheduler.start(task_func=run_unified_inspection, interval=interval)
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


# ============ 知识库 API（需 LLM 初始化后可用）============

# 知识库 Agent 需要嵌入模型，延迟初始化
_kb_agent = None

def get_kb_agent():
    global _kb_agent
    if _kb_agent is None:
        try:
            api_key = app_config.get("llm.api_key", "")
            base_url = app_config.get("llm.base_url", "")
            model = app_config.get("llm.model", "")
            if not api_key:
                from agents.knowledge_agent import KnowledgeBaseAgent
                _kb_agent = KnowledgeBaseAgent(llm_api_key="ollama", llm_base_url="http://localhost:11434/v1", llm_model="qwen3:8b")
            else:
                from agents.knowledge_agent import KnowledgeBaseAgent
                _kb_agent = KnowledgeBaseAgent(llm_api_key=api_key, llm_base_url=base_url, llm_model=model)
        except Exception as e:
            return None
    return _kb_agent

@app.post("/api/kb/ask")
async def api_kb_ask(question: str = Form(...)):
    kb = get_kb_agent()
    if kb is None:
        return {"result": "知识库引擎未就绪，请确认嵌入模型已下载且 LLM 配置正确"}
    return {"result": kb.query(question)}

@app.post("/api/kb/import")
async def api_kb_import(file: UploadFile = File(...)):
    kb = get_kb_agent()
    if kb is None:
        return {"result": "知识库引擎未就绪，请确认嵌入模型已下载且 LLM 配置正确"}
    # 保存上传文件到临时目录
    os.makedirs("data/uploads", exist_ok=True)
    file_path = os.path.join("data/uploads", file.filename)
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
        return {"result": "知识库引擎未就绪"}
    return {"result": kb.list_documents()}

@app.get("/api/kb/stats")
async def api_kb_stats():
    kb = get_kb_agent()
    if kb is None:
        return {"result": "知识库引擎未就绪"}
    return {"result": kb.get_stats()}

@app.post("/api/kb/delete")
async def api_kb_delete(doc_name: str = Form(...)):
    kb = get_kb_agent()
    if kb is None:
        return {"result": "知识库引擎未就绪"}
    return {"result": kb.delete_document(doc_name)}

@app.post("/api/kb/clear")
async def api_kb_clear():
    kb = get_kb_agent()
    if kb is None:
        return {"result": "知识库引擎未就绪"}
    return {"result": kb.clear_knowledge_base()}


# ============ 启动入口 ============

def run_server(host: str = "127.0.0.1", port: int = 7860):
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run_server()
