"""
Gradio网页界面模块
-----------------
企业级侧边栏布局，六大功能板块：
1. 巡检监控  2. 日志分析  3. 知识问答
4. 知识库    5. 诊断工具箱  6. 系统设置

布局: gr.Tabs 原生切换 + CSS 将标签栏转为左侧固定导航
"""

import os
import sys
import time
import threading
from datetime import datetime

import gradio as gr

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.inspection_agent import InspectionAgent, run_unified_inspection
from agents.log_analysis_agent import LogAnalysisAgent, analyze_log_content
from agents.knowledge_agent import KnowledgeBaseAgent
from agents.ssl_monitor import check_cert_expiry, batch_check_certs
from agents.network_diag import ping_host, check_tcp_port, dns_resolve, traceroute_host, http_health_check
from agents.db_inspector import (
    check_mysql_status, check_redis_status, show_mysql_slow_queries,
    check_mssql_status, check_oracle_status
)
from agents.security_audit import (
    audit_ssh_config, check_failed_logins, audit_firewall_rules,
    check_listening_ports, audit_cron_jobs
)
from utils.scheduler import InspectionScheduler
from utils.logger import get_logger
from utils.config import config as app_config
from utils.database import db

logger = get_logger(__name__)

# ========== 全局状态 ==========
_state_lock = threading.Lock()
inspection_agent: InspectionAgent = None
log_agent: LogAnalysisAgent = None
kb_agent: KnowledgeBaseAgent = None
_current_mode: str = "local"
scheduler = InspectionScheduler()

# ========== CSS ==========
CUSTOM_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

.gradio-container {
  max-width: 100% !important;
  margin: 0 !important;
  font-family: "Inter",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif !important;
  background: #f5f6f8 !important;
  color: #1a1a2e !important;
}
body { background: #f5f6f8 !important; }
footer { display: none !important; }

/* ===== Top-level Tabs → Sidebar Layout ===== */
/* Target Gradio 6.x tab structure: #main-tabs > tab-nav-container + tab-panels */
#main-tabs {
  display: flex !important;
  min-height: 100vh !important;
}

/* First child of tabs = the button bar. Make it a fixed left sidebar.
   Works with any class name Gradio 6.x generates. */
#main-tabs > div:first-child {
  display: flex !important;
  flex-direction: column !important;
  width: 220px !important;
  min-width: 220px !important;
  max-width: 220px !important;
  background: #fff !important;
  border-right: 1px solid #e8eaed !important;
  padding: 24px 0 !important;
  gap: 2px !important;
  position: fixed !important;
  top: 0 !important;
  left: 0 !important;
  bottom: 0 !important;
  z-index: 10 !important;
  overflow-y: auto !important;
}

/* All buttons inside the sidebar — style as nav items */
#main-tabs > div:first-child button {
  display: flex !important;
  align-items: center !important;
  gap: 10px !important;
  padding: 9px 20px !important;
  margin: 1px 8px !important;
  border: none !important;
  border-radius: 6px !important;
  font-size: 13px !important;
  font-weight: 500 !important;
  color: #5f6368 !important;
  background: transparent !important;
  text-align: left !important;
  white-space: nowrap !important;
  width: auto !important;
}
#main-tabs > div:first-child button:hover {
  background: #f5f6f8 !important;
  color: #1a1a2e !important;
}
#main-tabs > div:first-child button.selected,
#main-tabs > div:first-child button[aria-selected="true"],
#main-tabs > div:first-child button[data-selected="true"] {
  background: #eef0ff !important;
  color: #4f46e5 !important;
}

/* Logo spacer above nav buttons */
#main-tabs > div:first-child::before {
  content: "OA 运维助手";
  display: block;
  padding: 0 20px 20px 20px;
  margin: 0 8px 12px 8px;
  border-bottom: 1px solid #f0f0f3;
  font-size: 16px;
  font-weight: 700;
  color: #0f0f23;
}

/* All content panels — shift right to make room for sidebar */
#main-tabs > div:not(:first-child) {
  margin-left: 220px !important;
  padding: 28px 32px !important;
  min-height: 100vh !important;
  width: calc(100% - 220px) !important;
}

/* ===== Sub-tabs inside content (diagnostic toolbox) ===== */
/* Target tab containers that are NOT #main-tabs */
.tabs:not(#main-tabs) > div:first-child {
  border-bottom: 1px solid #e8eaed !important;
  flex-direction: row !important;
  position: static !important;
  width: auto !important;
  background: transparent !important;
  padding: 0 !important;
}
.tabs:not(#main-tabs) > div:first-child button {
  font-size: 13px !important;
  color: #6b7280 !important;
  padding: 8px 16px !important;
  border: none !important;
  border-bottom: 2px solid transparent !important;
  border-radius: 0 !important;
  background: transparent !important;
  margin: 0 !important;
}
.tabs:not(#main-tabs) > div:first-child button.selected,
.tabs:not(#main-tabs) > div:first-child button[aria-selected="true"] {
  color: #4f46e5 !important;
  border-bottom-color: #4f46e5 !important;
  background: transparent !important;
}

/* ===== Typography ===== */
.page-title {
  font-size: 20px;
  font-weight: 700;
  color: #0f0f23;
  margin-bottom: 4px;
}
.page-subtitle {
  font-size: 13px;
  color: #8b8fa3;
  margin-bottom: 24px;
}

/* ===== Cards ===== */
.ops-card {
  background: #fff !important;
  border: 1px solid #e8eaed !important;
  border-radius: 10px !important;
  padding: 20px 24px !important;
  margin-bottom: 16px !important;
  box-shadow: 0 1px 2px rgba(0,0,0,0.03) !important;
}

/* ===== Buttons ===== */
button, .gr-button {
  font-weight: 500 !important;
  font-size: 13px !important;
  border-radius: 6px !important;
  padding: 7px 14px !important;
  box-shadow: none !important;
}
.gr-button-primary {
  background: #4f46e5 !important;
  border: 1px solid #4f46e5 !important;
  color: #fff !important;
}
.gr-button-primary:hover { background: #4338ca !important; }
.gr-button-secondary {
  background: #fff !important;
  border: 1px solid #d1d5db !important;
  color: #374151 !important;
}
.gr-button-secondary:hover { background: #f9fafb !important; }
.gr-button-stop {
  background: #fff !important;
  border: 1px solid #fca5a5 !important;
  color: #dc2626 !important;
}
.gr-button-stop:hover { background: #fef2f2 !important; }

/* ===== Inputs ===== */
input, textarea {
  border: 1px solid #d1d5db !important;
  border-radius: 6px !important;
  font-size: 13px !important;
  padding: 8px 12px !important;
  color: #1a1a2e !important;
  background: #fff !important;
}
input:focus, textarea:focus {
  border-color: #4f46e5 !important;
  box-shadow: 0 0 0 3px rgba(79,70,229,0.1) !important;
  outline: none !important;
}
label { font-size: 12px !important; font-weight: 500 !important; color: #6b7280 !important; }
.gr-textbox textarea { resize: vertical !important; }
.gr-markdown { font-size: 13px !important; }
.gr-slider input[type="range"] { accent-color: #4f46e5 !important; }
"""

# ========== 运行模式预设 ==========
MODE_PRESETS = {
    "local": {
        "label": "本机离线 · Ollama qwen3:8b",
        "api_key": "ollama",
        "api_base": "http://localhost:11434/v1",
        "api_model": "qwen3:8b",
        "provider": "ollama",
    },
    "api": {
        "label": "云端在线 · DeepSeek",
        "api_key": app_config.get("llm.api_key", ""),
        "api_base": "https://api.deepseek.com/v1",
        "api_model": "deepseek-chat",
        "provider": "deepseek",
    },
}


def get_mode_info():
    preset = MODE_PRESETS.get(_current_mode, MODE_PRESETS["local"])
    return f"当前：{preset['label']}"


def switch_mode(mode, api_key, api_base, api_model):
    global _current_mode
    preset = MODE_PRESETS.get(mode)
    if not preset:
        return api_key, api_base, api_model, "未知模式"
    with _state_lock:
        _current_mode = mode
    if mode == "local":
        new_key, new_base, new_model = preset["api_key"], preset["api_base"], preset["api_model"]
        status = f"已切换为本地离线模式（{new_model}）"
    else:
        new_key = api_key if (api_key and api_key != "ollama") else preset["api_key"]
        new_base, new_model = preset["api_base"], preset["api_model"]
        status = f"已切换为云端在线模式（{new_model}）"
    app_config.set("llm.provider", preset["provider"])
    app_config.set("llm.model", new_model)
    app_config.set("llm.base_url", new_base)
    return new_key, new_base, new_model, status


# ========== 初始化函数 ==========

def init_agents(api_key: str, api_base: str, api_model: str) -> str:
    global inspection_agent, log_agent, kb_agent
    with _state_lock:
        is_local = (_current_mode == "local")
    if not is_local and (not api_key or api_key in ("your-api-key-here", "ollama")):
        return "⚠️ 请填写有效的云端 API 密钥，或切换到「本机离线」模式"

    mode_label = "本机离线" if is_local else "云端在线"

    yield f"[{mode_label}] 正在初始化巡检引擎..."
    try:
        new_insp = InspectionAgent(llm_api_key=api_key, llm_base_url=api_base, llm_model=api_model)
        with _state_lock:
            inspection_agent = new_insp
        yield f"[{mode_label}] ✓ 巡检引擎就绪\n正在初始化日志分析引擎..."
    except Exception as e:
        yield f"[{mode_label}] ✗ 巡检引擎失败：{e}"
        return

    try:
        new_log = LogAnalysisAgent(llm_api_key=api_key, llm_base_url=api_base, llm_model=api_model)
        with _state_lock:
            log_agent = new_log
        yield f"[{mode_label}] ✓ 巡检引擎就绪\n✓ 日志分析引擎就绪\n正在加载知识库引擎..."
    except Exception as e:
        with _state_lock:
            log_agent = None
        yield f"[{mode_label}] ✓ 巡检引擎就绪\n✗ 日志分析引擎失败：{e}\n正在加载知识库引擎..."

    try:
        new_kb = KnowledgeBaseAgent(llm_api_key=api_key, llm_base_url=api_base, llm_model=api_model)
        with _state_lock:
            kb_agent = new_kb
        yield f"[{mode_label}] ✓ 巡检引擎就绪\n✓ 日志分析引擎就绪\n✓ 知识库引擎就绪\n\n初始化完成！"
    except Exception as e:
        with _state_lock:
            kb_agent = None
        yield f"[{mode_label}] ✓ 巡检引擎就绪\n✓ 日志分析引擎就绪\n✗ 知识库引擎失败：{e}\n\n提示：巡检和日志分析可正常使用"


# ========== 巡检监控页 ==========

def start_inspection(interval: int) -> str:
    success = scheduler.start(task_func=run_unified_inspection, interval=interval)
    if success:
        mode = app_config.get("inspection.mode", "simulated")
        mode_label = {"ssh": "SSH远程", "local": "本机检测", "auto": "SSH优先+降级", "simulated": "模拟数据"}.get(mode, mode)
        return f"✅ 定时巡检已启动（{mode_label}）\n间隔: {interval}秒 | 日志: data/inspection_logs/"
    else:
        return "⚠️ 巡检已在运行中，请勿重复启动"


def stop_inspection() -> str:
    if scheduler.stop():
        return "⏹ 定时巡检已停止"
    return "⚠️ 巡检未在运行"


def adjust_interval(new_interval: int) -> str:
    if scheduler.adjust_interval(new_interval):
        return f"✅ 巡检间隔已调整为: {new_interval}秒"
    return "⚠️ 巡检未运行，请先启动巡检"


def manual_inspection() -> str:
    if inspection_agent is not None:
        try:
            return inspection_agent.run_inspection()
        except Exception as e:
            logger.warning(f"Agent巡检异常，降级: {e}")
    return run_unified_inspection()


def get_inspection_history(days: int = 7) -> str:
    try:
        summary = db.get_inspection_summary(days)
        if summary["total"] == 0:
            return f"最近{days}天暂无巡检记录"
        lines = [
            f"{'='*50}", f"  巡检历史统计（最近{days}天）", f"{'='*50}",
            f"总检测次数: {summary['total']}",
            f"  正常: {summary['normal']} | 告警: {summary['warning']} | 异常: {summary['error']}",
            f"",
        ]
        for name, info in summary.get("by_type", {}).items():
            icon = "⚠️" if info["abnormal"] > 0 else "✅"
            lines.append(f"  {icon} {name}: {info['total']}次, 异常{info['abnormal']}次")
        lines.append(f"\n{'─'*50}\n最近记录:")
        records = db.get_inspection_history(days=days, limit=10)
        for r in records:
            icon = {"normal": "✅", "warning": "⚠️", "error": "❌"}.get(r["status"], "❓")
            lines.append(f"  {icon} [{r['check_time'][:19]}] {r['check_type_cn']}: {r['status']}")
        return "\n".join(lines)
    except Exception as e:
        return f"[错误] 查询历史失败: {e}"


def get_db_overview() -> str:
    try:
        stats = db.get_db_stats()
        return (
            f"📊 数据库统计\n"
            f"  巡检记录: {stats.get('inspection_records', 0)} 条\n"
            f"  告警记录: {stats.get('alert_history', 0)} 条\n"
            f"  分析记录: {stats.get('log_analysis_records', 0)} 条\n"
            f"  存储位置: {stats.get('db_path', '未知')}"
        )
    except Exception as e:
        return f"[错误] {e}"


def get_scheduler_status() -> str:
    running = "运行中" if scheduler.is_running else "已停止"
    mode = app_config.get("inspection.mode", "simulated")
    mode_label = {"ssh": "SSH远程", "local": "本机", "auto": "SSH优先", "simulated": "模拟"}.get(mode, mode)
    return f"调度器: {running} | 巡检模式: {mode_label} | 间隔: {scheduler.interval}秒"


# ========== 日志分析页 ==========

def analyze_log_file(file_obj) -> str:
    if file_obj is None:
        return "⚠️ 请先上传日志文件（.txt格式）"
    try:
        with open(file_obj.name, "r", encoding="utf-8", errors="ignore") as f:
            log_text = f.read()
    except Exception as e:
        return f"[错误] 无法读取文件: {str(e)}"
    return _do_log_analysis(log_text)


def analyze_log_text(log_text: str) -> str:
    if not log_text or not log_text.strip():
        return "⚠️ 请输入需要分析的日志内容"
    return _do_log_analysis(log_text)


def _do_log_analysis(log_text: str) -> str:
    if log_agent is not None:
        try:
            return log_agent.analyze(log_text)
        except Exception as e:
            logger.warning(f"Agent分析异常，降级: {e}")
    return analyze_log_content.invoke({"log_text": log_text})


# ========== 知识库页 ==========

def kb_ask_question(question: str) -> str:
    if kb_agent is None:
        return "⚠️ 请先初始化系统（知识库Agent需要加载嵌入模型）"
    if not question or not question.strip():
        return "⚠️ 请输入您想咨询的运维问题"
    return kb_agent.query(question)


def kb_import_document(file_obj) -> str:
    if kb_agent is None:
        return "⚠️ 请先初始化系统"
    if file_obj is None:
        return "⚠️ 请先选择要上传的文档（.pdf/.docx/.txt）"
    return kb_agent.import_document(file_obj.name)


def kb_list_docs() -> str:
    if kb_agent is None:
        return "⚠️ 请先初始化系统"
    return kb_agent.list_documents()


def kb_delete_doc(doc_name: str) -> str:
    if kb_agent is None:
        return "⚠️ 请先初始化系统"
    if not doc_name or not doc_name.strip():
        return "⚠️ 请输入要删除的文档文件名"
    return kb_agent.delete_document(doc_name.strip())


def kb_get_stats() -> str:
    if kb_agent is None:
        return "⚠️ 请先初始化系统"
    return kb_agent.get_stats()


def kb_clear_all() -> str:
    if kb_agent is None:
        return "⚠️ 请先初始化系统"
    return kb_agent.clear_knowledge_base()


# ========== 构建Gradio界面 ==========

def create_ui():
    """使用 gr.Tabs + CSS flex 实现侧边栏布局"""

    with gr.Blocks(title="OA 运维助手", css=CUSTOM_CSS) as app:

        with gr.Tabs(elem_id="main-tabs"):

            # ===========================================================
            # Tab 1: 巡检监控
            # ===========================================================
            with gr.TabItem("📊  巡检监控"):
                gr.HTML('<div class="page-title">📊 巡检监控</div>')
                gr.HTML('<div class="page-subtitle">自动化服务器巡检 · 端口/服务/磁盘/内存 · 定时调度</div>')

                with gr.Row():
                    with gr.Column(scale=1):
                        with gr.Group(elem_classes=["ops-card"]):
                            interval_slider = gr.Slider(minimum=10, maximum=300, value=30, step=10, label="巡检间隔（秒）")
                            with gr.Row():
                                start_btn = gr.Button("定时巡检", variant="primary")
                                stop_btn = gr.Button("停止", variant="stop")
                            with gr.Row():
                                adjust_btn = gr.Button("调整间隔", variant="secondary")
                                manual_btn = gr.Button("立即巡检", variant="secondary")

                    with gr.Column(scale=2):
                        with gr.Group(elem_classes=["ops-card"]):
                            scheduler_status = gr.Textbox(label="调度器", value=get_scheduler_status(), lines=1, interactive=False)
                            inspection_output = gr.Textbox(label="巡检结果", placeholder="执行巡检后结果将显示在此处...", lines=18, max_lines=28)

                start_btn.click(fn=start_inspection, inputs=[interval_slider], outputs=[inspection_output]).then(fn=get_scheduler_status, outputs=[scheduler_status])
                stop_btn.click(fn=stop_inspection, outputs=[inspection_output]).then(fn=get_scheduler_status, outputs=[scheduler_status])
                adjust_btn.click(fn=adjust_interval, inputs=[interval_slider], outputs=[inspection_output]).then(fn=get_scheduler_status, outputs=[scheduler_status])
                manual_btn.click(fn=manual_inspection, outputs=[inspection_output])

                timer = gr.Timer(5)
                timer.tick(fn=get_scheduler_status, outputs=[scheduler_status])

                with gr.Group(elem_classes=["ops-card"]):
                    gr.Markdown("#### 📜 历史记录")
                    with gr.Row():
                        history_btn = gr.Button("巡检历史", variant="secondary")
                        history_days = gr.Slider(1, 30, 7, step=1, label="天数")
                        db_btn = gr.Button("数据库统计", variant="secondary")
                    history_output = gr.Textbox(label="历史记录", lines=10, max_lines=18)
                    history_btn.click(fn=get_inspection_history, inputs=[history_days], outputs=[history_output])
                    db_btn.click(fn=get_db_overview, outputs=[history_output])

            # ===========================================================
            # Tab 2: 日志分析
            # ===========================================================
            with gr.TabItem("📋  日志分析"):
                gr.HTML('<div class="page-title">📋 日志分析</div>')
                gr.HTML('<div class="page-subtitle">上传或粘贴运维日志 · 自动匹配故障模式 · 生成排查建议</div>')

                with gr.Group(elem_classes=["ops-card"]):
                    with gr.Row():
                        log_file_input = gr.File(label="上传日志文件", file_types=[".txt", ".log"], scale=1)
                        log_text_input = gr.Textbox(label="或粘贴日志内容", placeholder="在此粘贴日志...", lines=8, scale=2)
                    with gr.Row():
                        analyze_file_btn = gr.Button("分析文件", variant="primary")
                        analyze_text_btn = gr.Button("分析内容", variant="secondary")
                        clear_log_btn = gr.Button("清空", variant="stop")
                    log_result = gr.Textbox(label="分析报告", placeholder="分析结果将显示在此处...", lines=16, max_lines=24)

                analyze_file_btn.click(fn=analyze_log_file, inputs=[log_file_input], outputs=[log_result])
                analyze_text_btn.click(fn=analyze_log_text, inputs=[log_text_input], outputs=[log_result])
                clear_log_btn.click(fn=lambda: ("", "", ""), outputs=[log_file_input, log_text_input, log_result])

            # ===========================================================
            # Tab 3: 知识问答
            # ===========================================================
            with gr.TabItem("💬  知识问答"):
                gr.HTML('<div class="page-title">💬 知识问答</div>')
                gr.HTML('<div class="page-subtitle">基于 RAG 的运维知识智能问答 · 回答严格依据知识库文档</div>')

                with gr.Group(elem_classes=["ops-card"]):
                    with gr.Row():
                        kb_question_input = gr.Textbox(label="运维问题", placeholder="例如：OA 审批流程卡死了怎么办？", lines=2, scale=4)
                        kb_ask_btn = gr.Button("提问", variant="primary", scale=1)
                    kb_answer_output = gr.Textbox(label="回答", placeholder="AI 将基于知识库为您解答...", lines=16, max_lines=24)

                kb_ask_btn.click(fn=kb_ask_question, inputs=[kb_question_input], outputs=[kb_answer_output])

            # ===========================================================
            # Tab 4: 知识库管理
            # ===========================================================
            with gr.TabItem("📚  知识库"):
                gr.HTML('<div class="page-title">📚 知识库</div>')
                gr.HTML('<div class="page-subtitle">上传运维文档 · 自动分块向量化 · 智能检索问答</div>')

                with gr.Group(elem_classes=["ops-card"]):
                    with gr.Row():
                        with gr.Column(scale=1):
                            kb_upload_file = gr.File(label="上传文档", file_types=[".pdf", ".docx", ".txt"])
                            with gr.Row():
                                kb_import_btn = gr.Button("导入", variant="primary")
                                kb_stats_btn = gr.Button("统计", variant="secondary")
                        with gr.Column(scale=2):
                            kb_import_result = gr.Textbox(label="导入结果", placeholder="导入结果将显示在此处...", lines=6)
                    gr.Markdown("---")
                    with gr.Row():
                        kb_list_btn = gr.Button("全部文档", variant="secondary")
                        kb_delete_name = gr.Textbox(label="删除文档", placeholder="输入文件名", scale=3)
                        kb_delete_btn = gr.Button("删除", variant="stop")
                        kb_clear_btn = gr.Button("清空知识库", variant="stop")
                    kb_doc_list_output = gr.Textbox(label="文档清单", placeholder="点击「全部文档」查看...", lines=10)

                kb_import_btn.click(fn=kb_import_document, inputs=[kb_upload_file], outputs=[kb_import_result])
                kb_list_btn.click(fn=kb_list_docs, outputs=[kb_doc_list_output])
                kb_delete_btn.click(fn=kb_delete_doc, inputs=[kb_delete_name], outputs=[kb_doc_list_output])
                kb_stats_btn.click(fn=kb_get_stats, outputs=[kb_doc_list_output])
                kb_clear_btn.click(fn=kb_clear_all, outputs=[kb_doc_list_output])

            # ===========================================================
            # Tab 5: 诊断工具箱
            # ===========================================================
            with gr.TabItem("🛠  诊断工具箱"):
                gr.HTML('<div class="page-title">🛠 诊断工具箱</div>')
                gr.HTML('<div class="page-subtitle">SSL证书 · 网络诊断 · 数据库巡检 · 安全基线 — 开箱即用</div>')

                with gr.Tabs():
                    # ---- SSL ----
                    with gr.TabItem("🔒 SSL证书"):
                        with gr.Group(elem_classes=["ops-card"]):
                            with gr.Row():
                                ssl_domain = gr.Textbox(label="域名", placeholder="oa.example.com 或 oa.example.com:8443", scale=2)
                                ssl_check_btn = gr.Button("检测证书", variant="primary", scale=1)
                            ssl_single_result = gr.Textbox(label="检测结果", placeholder="输入域名后点击检测...", lines=14, max_lines=20)
                            gr.Markdown("---")
                            ssl_batch_input = gr.Textbox(label="批量检测（每行一个域名）", placeholder="oa.example.com\nwww.example.com", lines=3)
                            ssl_batch_btn = gr.Button("批量检测", variant="secondary")
                            ssl_batch_result = gr.Textbox(label="批量结果", lines=12, max_lines=18)

                        ssl_check_btn.click(fn=lambda d: check_cert_expiry.invoke({"domain": d}) if d.strip() else "请输入域名", inputs=[ssl_domain], outputs=[ssl_single_result])
                        ssl_batch_btn.click(fn=lambda t: batch_check_certs.invoke({"domains_text": t}), inputs=[ssl_batch_input], outputs=[ssl_batch_result])

                    # ---- Network ----
                    with gr.TabItem("🌐 网络诊断"):
                        with gr.Group(elem_classes=["ops-card"]):
                            with gr.Row():
                                net_target = gr.Textbox(label="目标地址", placeholder="IP、域名 或 host:port", scale=3)
                                ping_btn = gr.Button("Ping", variant="primary")
                                port_btn = gr.Button("端口", variant="primary")
                                dns_btn = gr.Button("DNS", variant="primary")
                                trace_btn = gr.Button("路由", variant="secondary")
                                http_btn = gr.Button("HTTP", variant="secondary")
                            net_result = gr.Textbox(label="诊断结果", placeholder="选择诊断方式后结果将显示在此处...", lines=16, max_lines=24)

                        ping_btn.click(fn=lambda t: ping_host.invoke({"host": t}) if t.strip() else "请输入目标地址", inputs=[net_target], outputs=[net_result])
                        port_btn.click(fn=lambda t: check_tcp_port.invoke({"host_port": t}) if t.strip() else "请输入 host:port", inputs=[net_target], outputs=[net_result])
                        dns_btn.click(fn=lambda t: dns_resolve.invoke({"domain": t}) if t.strip() else "请输入域名", inputs=[net_target], outputs=[net_result])
                        trace_btn.click(fn=lambda t: traceroute_host.invoke({"host": t}) if t.strip() else "请输入目标地址", inputs=[net_target], outputs=[net_result])
                        http_btn.click(fn=lambda t: http_health_check.invoke({"url": t}) if t.strip() else "请输入URL", inputs=[net_target], outputs=[net_result])

                    # ---- Database ----
                    with gr.TabItem("🗄️ 数据库"):
                        with gr.Group(elem_classes=["ops-card"]):
                            gr.Markdown("**MySQL**")
                            with gr.Row():
                                db_host = gr.Textbox(label="主机", value="127.0.0.1", scale=2)
                                db_port = gr.Number(label="端口", value=3306, precision=0)
                                db_user = gr.Textbox(label="用户", value="root")
                                db_pass = gr.Textbox(label="密码", type="password")
                            with gr.Row():
                                mysql_check_btn = gr.Button("健康检查", variant="primary")
                                mysql_slow_btn = gr.Button("慢查询", variant="secondary")
                            db_mysql_result = gr.Textbox(label="MySQL 结果", lines=12, max_lines=18)

                        mysql_check_btn.click(fn=lambda h, p, u, pw: check_mysql_status.invoke({"config_text": f"host={h} port={int(p)} user={u} password={pw}"}), inputs=[db_host, db_port, db_user, db_pass], outputs=[db_mysql_result])
                        mysql_slow_btn.click(fn=lambda h, p, u, pw: show_mysql_slow_queries.invoke({"config_text": f"host={h} port={int(p)} user={u} password={pw} limit=20"}), inputs=[db_host, db_port, db_user, db_pass], outputs=[db_mysql_result])

                        with gr.Group(elem_classes=["ops-card"]):
                            gr.Markdown("**Redis**")
                            with gr.Row():
                                redis_host = gr.Textbox(label="主机", value="127.0.0.1", scale=2)
                                redis_port = gr.Number(label="端口", value=6379, precision=0)
                                redis_pass = gr.Textbox(label="密码", type="password")
                                redis_check_btn = gr.Button("健康检查", variant="primary")
                            db_redis_result = gr.Textbox(label="Redis 结果", lines=12, max_lines=18)

                        redis_check_btn.click(fn=lambda h, p, pw: check_redis_status.invoke({"config_text": f"host={h} port={int(p)} password={pw}"}), inputs=[redis_host, redis_port, redis_pass], outputs=[db_redis_result])

                        with gr.Group(elem_classes=["ops-card"]):
                            gr.Markdown("**SQL Server**")
                            with gr.Row():
                                ms_host = gr.Textbox(label="主机", value="127.0.0.1", scale=2)
                                ms_port = gr.Number(label="端口", value=1433, precision=0)
                                ms_user = gr.Textbox(label="用户", value="sa")
                                ms_pass = gr.Textbox(label="密码", type="password")
                                ms_check_btn = gr.Button("健康检查", variant="primary")
                            db_mssql_result = gr.Textbox(label="SQL Server 结果", lines=12, max_lines=18)

                        ms_check_btn.click(fn=lambda h, p, u, pw: check_mssql_status.invoke({"config_text": f"host={h} port={int(p)} user={u} password={pw}"}), inputs=[ms_host, ms_port, ms_user, ms_pass], outputs=[db_mssql_result])

                        with gr.Group(elem_classes=["ops-card"]):
                            gr.Markdown("**Oracle**")
                            with gr.Row():
                                ora_host = gr.Textbox(label="主机", value="127.0.0.1", scale=2)
                                ora_port = gr.Number(label="端口", value=1521, precision=0)
                                ora_user = gr.Textbox(label="用户", value="system")
                                ora_pass = gr.Textbox(label="密码", type="password")
                                ora_svc = gr.Textbox(label="服务名", value="orcl")
                                ora_check_btn = gr.Button("健康检查", variant="primary")
                            db_oracle_result = gr.Textbox(label="Oracle 结果", lines=12, max_lines=18)

                        ora_check_btn.click(fn=lambda h, p, u, pw, svc: check_oracle_status.invoke({"config_text": f"host={h} port={int(p)} user={u} password={pw} service={svc}"}), inputs=[ora_host, ora_port, ora_user, ora_pass, ora_svc], outputs=[db_oracle_result])

                    # ---- Security ----
                    with gr.TabItem("🔐 安全基线"):
                        with gr.Group(elem_classes=["ops-card"]):
                            gr.Markdown("**单项审计**")
                            with gr.Row():
                                sec_ssh_btn = gr.Button("SSH配置", variant="primary")
                                sec_login_btn = gr.Button("失败登录", variant="primary")
                                sec_fw_btn = gr.Button("防火墙", variant="primary")
                            with gr.Row():
                                sec_ports_btn = gr.Button("监听端口", variant="secondary")
                                sec_cron_btn = gr.Button("Crontab", variant="secondary")
                                sec_all_btn = gr.Button("一键全量审计", variant="stop")
                            sec_result = gr.Textbox(label="审计结果", lines=18, max_lines=26)

                        sec_ssh_btn.click(fn=lambda: audit_ssh_config.invoke({}), outputs=[sec_result])
                        sec_login_btn.click(fn=lambda: check_failed_logins.invoke({}), outputs=[sec_result])
                        sec_fw_btn.click(fn=lambda: audit_firewall_rules.invoke({}), outputs=[sec_result])
                        sec_ports_btn.click(fn=lambda: check_listening_ports.invoke({}), outputs=[sec_result])
                        sec_cron_btn.click(fn=lambda: audit_cron_jobs.invoke({}), outputs=[sec_result])
                        sec_all_btn.click(fn=lambda: (
                            "=" * 55 + "\n  全量安全基线审计报告\n" + "=" * 55 + "\n\n" +
                            audit_ssh_config.invoke({}) + "\n\n" + check_failed_logins.invoke({}) + "\n\n" +
                            audit_firewall_rules.invoke({}) + "\n\n" + check_listening_ports.invoke({}) + "\n\n" +
                            audit_cron_jobs.invoke({})), outputs=[sec_result])

            # ===========================================================
            # Tab 6: 系统设置
            # ===========================================================
            with gr.TabItem("⚙️  系统设置"):
                gr.HTML('<div class="page-title">⚙️ 系统设置</div>')
                gr.HTML('<div class="page-subtitle">LLM 后端配置 · 模型选择 · 运行模式</div>')

                with gr.Group(elem_classes=["ops-card"]):
                    mode_radio = gr.Radio(
                        choices=[(MODE_PRESETS["local"]["label"], "local"), (MODE_PRESETS["api"]["label"], "api")],
                        value="local", label="运行模式", interactive=True,
                    )
                    with gr.Row():
                        api_key_input = gr.Textbox(label="API Key", placeholder="离线模式无需填写", value=MODE_PRESETS["local"]["api_key"], type="password", interactive=False, scale=3)
                        api_base_input = gr.Textbox(label="接口地址", value=MODE_PRESETS["local"]["api_base"], interactive=False, scale=2)
                        api_model_input = gr.Textbox(label="模型", value=MODE_PRESETS["local"]["api_model"], interactive=False, scale=2)
                    with gr.Row():
                        init_btn = gr.Button("应用并初始化", variant="primary")
                        mode_status = gr.Textbox(label="状态", value=get_mode_info(), lines=1, interactive=False)
                    init_status = gr.Textbox(label="初始化日志", value="就绪", lines=3)

                def on_mode_change(mode):
                    preset = MODE_PRESETS[mode]
                    return preset["api_key"], preset["api_base"], preset["api_model"], preset["label"]

                mode_radio.change(fn=on_mode_change, inputs=[mode_radio], outputs=[api_key_input, api_base_input, api_model_input, mode_status])

                def do_init(mode, api_key, api_base, api_model):
                    new_key, new_base, new_model, switch_msg = switch_mode(mode, api_key, api_base, api_model)
                    mode_info = get_mode_info()
                    for step_msg in init_agents(new_key, new_base, new_model):
                        yield new_key, new_base, new_model, mode_info, switch_msg + "\n" + step_msg

                init_btn.click(fn=do_init, inputs=[mode_radio, api_key_input, api_base_input, api_model_input], outputs=[api_key_input, api_base_input, api_model_input, mode_status, init_status])

    return app


# ========== 启动入口 ==========

if __name__ == "__main__":
    app = create_ui()
    app.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        inbrowser=True,
        show_error=True,
    )
