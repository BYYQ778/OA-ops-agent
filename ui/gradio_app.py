"""
Gradio网页界面模块
-----------------
提供四大功能板块的网页操作界面：
1. 巡检监控 - 启停定时巡检、调整间隔、查看实时状态
2. 日志分析 - 上传日志文件/粘贴日志内容、获取故障分析报告
3. 知识库问答 - 基于RAG的运维知识智能问答
4. 知识库管理 - 上传/查看/删除运维文档

布局设计: 使用Gradio Tab分页，每页功能独立，操作简洁明了。
"""

import os
import sys
import time
import threading
from datetime import datetime

import gradio as gr

# 将项目根目录加入Python路径（确保直接运行此文件也能正常导入）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.inspection_agent import InspectionAgent, run_unified_inspection
from agents.log_analysis_agent import LogAnalysisAgent, analyze_log_content
from agents.knowledge_agent import KnowledgeBaseAgent
from utils.scheduler import InspectionScheduler
from utils.logger import get_logger
from utils.config import config as app_config
from utils.database import db

logger = get_logger(__name__)

# ========== 全局状态 ==========
# 各Agent实例（延迟初始化，等待用户配置API密钥）
_state_lock = threading.Lock()
inspection_agent: InspectionAgent = None
log_agent: LogAnalysisAgent = None
kb_agent: KnowledgeBaseAgent = None
_current_mode: str = "local"
scheduler = InspectionScheduler()

# ========== CSS样式 ==========
CUSTOM_CSS = """
/* ===== Design System: Modern SaaS / Linear-inspired ===== */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');

:root {
  --gray-50:  #f8f9fb;
  --gray-100: #f1f3f5;
  --gray-200: #e5e7eb;
  --gray-300: #d1d5db;
  --gray-400: #9ca3af;
  --gray-500: #6b7280;
  --gray-600: #4b5563;
  --gray-700: #374151;
  --gray-800: #1f2937;
  --gray-900: #111827;
  --blue-50:  #eff6ff;
  --blue-100: #dbeafe;
  --blue-500: #3b82f6;
  --blue-600: #2563eb;
  --blue-700: #1d4ed8;
  --green-500:#10b981;
  --green-100:#d1fae5;
  --red-500: #ef4444;
  --red-100: #fee2e2;
  --amber-500:#f59e0b;
  --amber-100:#fef3c7;
  --radius-sm: 6px;
  --radius-md: 8px;
  --radius-lg: 12px;
  --shadow-sm: 0 1px 2px rgba(0,0,0,.04);
  --shadow-md: 0 4px 12px rgba(0,0,0,.06);
}

/* ---- Base ---- */
.gradio-container {
  max-width: 1100px !important;
  margin: 0 auto !important;
  font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif !important;
  background: #fff !important;
  color: var(--gray-800) !important;
}
body { background: var(--gray-50) !important; }

/* ---- Typography ---- */
h1, h2, h3, h4 { font-weight: 600 !important; color: var(--gray-900) !important; letter-spacing: -0.01em !important; }
.prose { color: var(--gray-600) !important; font-size: 14px !important; line-height: 1.6 !important; }

/* ---- Header ---- */
.app-header {
  padding: 28px 0 12px 0;
  border-bottom: 1px solid var(--gray-100);
  margin-bottom: 20px;
}
.app-header h1 {
  font-size: 22px !important;
  font-weight: 600 !important;
  color: var(--gray-900) !important;
  margin: 0 0 4px 0 !important;
  letter-spacing: -0.02em !important;
}
.app-header p {
  font-size: 13px !important;
  color: var(--gray-400) !important;
  margin: 0 !important;
}

/* ---- Cards ---- */
.card {
  background: #fff;
  border: 1px solid var(--gray-200);
  border-radius: var(--radius-lg);
  padding: 20px 24px;
  margin-bottom: 16px;
  box-shadow: var(--shadow-sm);
}

/* ---- Tabs ---- */
.tabs {
  border-bottom: 1px solid var(--gray-200) !important;
  gap: 0 !important;
}
.tabs > .tab-nav > button, .tabs > button {
  font-size: 14px !important;
  font-weight: 500 !important;
  color: var(--gray-500) !important;
  padding: 10px 18px !important;
  border: none !important;
  border-bottom: 2px solid transparent !important;
  border-radius: 0 !important;
  background: transparent !important;
  margin: 0 !important;
  transition: all .15s ease !important;
}
.tabs > .tab-nav > button:hover, .tabs > button:hover {
  color: var(--gray-800) !important;
  background: var(--gray-50) !important;
}
.tabs > .tab-nav > button.selected, .tabs > button.selected {
  color: var(--blue-600) !important;
  border-bottom-color: var(--blue-600) !important;
  background: transparent !important;
}

/* ---- Buttons ---- */
button, .gr-button {
  font-weight: 500 !important;
  font-size: 13px !important;
  border-radius: var(--radius-sm) !important;
  padding: 8px 16px !important;
  transition: all .15s ease !important;
  letter-spacing: 0.01em !important;
}
.gr-button-primary, button[variant="primary"] {
  background: var(--gray-900) !important;
  border: 1px solid var(--gray-900) !important;
  color: #fff !important;
}
.gr-button-primary:hover, button[variant="primary"]:hover {
  background: var(--gray-700) !important;
  border-color: var(--gray-700) !important;
}
.gr-button-secondary, button[variant="secondary"] {
  background: #fff !important;
  border: 1px solid var(--gray-200) !important;
  color: var(--gray-700) !important;
}
.gr-button-secondary:hover, button[variant="secondary"]:hover {
  background: var(--gray-50) !important;
  border-color: var(--gray-300) !important;
}
.gr-button-stop, button[variant="stop"] {
  background: #fff !important;
  border: 1px solid var(--red-500) !important;
  color: var(--red-500) !important;
}
.gr-button-stop:hover, button[variant="stop"]:hover {
  background: var(--red-100) !important;
}

/* ---- Inputs ---- */
input, textarea, .gr-textbox input, .gr-textbox textarea {
  border: 1px solid var(--gray-200) !important;
  border-radius: var(--radius-sm) !important;
  font-size: 14px !important;
  padding: 8px 12px !important;
  transition: border-color .15s ease, box-shadow .15s ease !important;
  color: var(--gray-800) !important;
  background: #fff !important;
}
input:focus, textarea:focus, .gr-textbox input:focus, .gr-textbox textarea:focus {
  border-color: var(--blue-500) !important;
  box-shadow: 0 0 0 3px rgba(59,130,246,.12) !important;
  outline: none !important;
}

/* ---- Labels ---- */
label, .gr-radio label, .gr-checkbox label {
  font-size: 13px !important;
  font-weight: 500 !important;
  color: var(--gray-600) !important;
  margin-bottom: 4px !important;
}

/* ---- Accordion ---- */
.gr-accordion, .accordion {
  border: 1px solid var(--gray-200) !important;
  border-radius: var(--radius-lg) !important;
  box-shadow: var(--shadow-sm) !important;
  margin-bottom: 20px !important;
}
.gr-accordion .label-wrap, .accordion > .label-wrap {
  font-weight: 600 !important;
  font-size: 14px !important;
  color: var(--gray-800) !important;
  padding: 14px 20px !important;
}

/* ---- Radio Group ---- */
.gr-radio {
  background: var(--gray-50) !important;
  border: 1px solid var(--gray-200) !important;
  border-radius: var(--radius-md) !important;
  padding: 8px 14px !important;
}

/* ---- File Upload ---- */
.gr-file {
  border: 2px dashed var(--gray-200) !important;
  border-radius: var(--radius-md) !important;
  background: var(--gray-50) !important;
  transition: border-color .15s ease !important;
}
.gr-file:hover { border-color: var(--gray-300) !important; }

/* ---- Slider ---- */
.gr-slider input[type="range"] {
  accent-color: var(--gray-900) !important;
}

/* ---- Footer ---- */
.app-footer {
  margin-top: 32px;
  padding: 16px 0;
  border-top: 1px solid var(--gray-100);
  color: var(--gray-400);
  font-size: 12px;
}

/* ---- Status indicators ---- */
.scheduler-status {
  font-size: 13px !important;
  padding: 10px 14px !important;
  background: var(--gray-50) !important;
  border: 1px solid var(--gray-200) !important;
  border-radius: var(--radius-sm) !important;
}

/* ---- Misc ---- */
footer { display: none !important; }
.gr-markdown { font-size: 14px !important; }
.gr-box { border-radius: var(--radius-md) !important; }
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
    """使用用户配置的API参数初始化所有Agent，逐步反馈进度。"""
    global inspection_agent, log_agent, kb_agent
    with _state_lock:
        is_local = (_current_mode == "local")
    if not is_local and (not api_key or api_key in ("your-api-key-here", "ollama")):
        return "⚠️ 请填写有效的云端 API 密钥，或切换到「本机离线」模式"

    mode_label = "本机离线" if is_local else "云端在线"

    # Step 1: 巡检引擎（轻量，秒级）
    yield f"[{mode_label}] 正在初始化巡检引擎..."
    try:
        new_insp = InspectionAgent(llm_api_key=api_key, llm_base_url=api_base, llm_model=api_model)
        with _state_lock:
            inspection_agent = new_insp
        yield f"[{mode_label}] ✓ 巡检引擎就绪\n正在初始化日志分析引擎..."
    except Exception as e:
        yield f"[{mode_label}] ✗ 巡检引擎失败：{e}"
        return

    # Step 2: 日志分析引擎（轻量，秒级）
    try:
        new_log = LogAnalysisAgent(llm_api_key=api_key, llm_base_url=api_base, llm_model=api_model)
        with _state_lock:
            log_agent = new_log
        yield f"[{mode_label}] ✓ 巡检引擎就绪\n✓ 日志分析引擎就绪\n正在加载知识库引擎（加载嵌入模型，约 5-15 秒）..."
    except Exception as e:
        with _state_lock:
            log_agent = None
        yield f"[{mode_label}] ✓ 巡检引擎就绪\n✗ 日志分析引擎失败：{e}\n正在加载知识库引擎..."

    # Step 3: 知识库引擎（重，需要加载嵌入模型，5-15秒）
    try:
        new_kb = KnowledgeBaseAgent(llm_api_key=api_key, llm_base_url=api_base, llm_model=api_model)
        with _state_lock:
            kb_agent = new_kb
        yield f"[{mode_label}] ✓ 巡检引擎就绪\n✓ 日志分析引擎就绪\n✓ 知识库引擎就绪（嵌入模型已加载）\n\n初始化完成！"
    except Exception as e:
        with _state_lock:
            kb_agent = None
        yield f"[{mode_label}] ✓ 巡检引擎就绪\n✓ 日志分析引擎就绪\n✗ 知识库引擎失败：{e}\n\n提示：巡检和日志分析可正常使用，知识库需嵌入模型就绪后可用"

    return


# ========== 巡检监控页 ==========

def start_inspection(interval: int) -> str:
    """启动定时巡检"""
    success = scheduler.start(
        task_func=run_unified_inspection,
        interval=interval
    )
    if success:
        mode = app_config.get("inspection.mode", "simulated")
        mode_label = {"ssh": "SSH远程", "local": "本机检测", "auto": "SSH优先+降级", "simulated": "模拟数据"}.get(mode, mode)
        return f"✅ 定时巡检已启动（{mode_label}）\n间隔: {interval}秒 | 日志: data/inspection_logs/ | 数据库: data/oa_ops.db"
    else:
        return "⚠️ 巡检已在运行中，请勿重复启动"


def stop_inspection() -> str:
    """停止定时巡检"""
    if scheduler.stop():
        return "⏹ 定时巡检已停止"
    return "⚠️ 巡检未在运行"


def adjust_interval(new_interval: int) -> str:
    """调整巡检间隔"""
    if scheduler.adjust_interval(new_interval):
        return f"✅ 巡检间隔已调整为: {new_interval}秒"
    return "⚠️ 巡检未运行，请先启动巡检"


def manual_inspection() -> str:
    """手动执行一次巡检"""
    if inspection_agent is not None:
        try:
            return inspection_agent.run_inspection()
        except Exception as e:
            logger.warning(f"Agent巡检异常，降级为统一巡检: {e}")
    return run_unified_inspection()


def get_inspection_history(days: int = 7) -> str:
    """查询巡检历史记录"""
    try:
        summary = db.get_inspection_summary(days)
        if summary["total"] == 0:
            return f"最近{days}天暂无巡检记录"

        lines = [
            f"{'='*50}",
            f"  巡检历史统计（最近{days}天）",
            f"{'='*50}",
            f"总检测次数: {summary['total']}",
            f"  正常: {summary['normal']} | 告警: {summary['warning']} | 异常: {summary['error']}",
            f"",
        ]
        for name, info in summary.get("by_type", {}).items():
            status_icon = "⚠️" if info["abnormal"] > 0 else "✅"
            lines.append(f"  {status_icon} {name}: {info['total']}次, 异常{info['abnormal']}次")

        # 最近N条记录
        lines.append(f"\n{'─'*50}")
        lines.append("最近记录:")
        records = db.get_inspection_history(days=days, limit=10)
        for r in records:
            icon = {"normal": "✅", "warning": "⚠️", "error": "❌"}.get(r["status"], "❓")
            lines.append(f"  {icon} [{r['check_time'][:19]}] {r['check_type_cn']}: {r['status']}")

        return "\n".join(lines)
    except Exception as e:
        return f"[错误] 查询历史失败: {e}"


def get_db_overview() -> str:
    """获取数据库概览"""
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
    """获取当前调度器状态"""
    running = "运行中" if scheduler.is_running else "已停止"
    mode = app_config.get("inspection.mode", "simulated")
    mode_label = {"ssh": "SSH远程", "local": "本机", "auto": "SSH优先", "simulated": "模拟"}.get(mode, mode)
    return f"调度器: {running} | 巡检模式: {mode_label} | 间隔: {scheduler.interval}秒"



# ========== 日志分析页 ==========

def analyze_log_file(file_obj) -> str:
    """分析上传的日志文件"""
    if file_obj is None:
        return "⚠️ 请先上传日志文件（.txt格式）"

    file_path = file_obj.name
    logger.info(f"收到上传日志文件: {file_path}")

    # 读取文件内容
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            log_text = f.read()
    except Exception as e:
        return f"[错误] 无法读取文件: {str(e)}"

    return _do_log_analysis(log_text)


def analyze_log_text(log_text: str) -> str:
    """分析粘贴的日志文本"""
    if not log_text or not log_text.strip():
        return "⚠️ 请输入需要分析的日志内容"

    return _do_log_analysis(log_text)


def _do_log_analysis(log_text: str) -> str:
    """执行日志分析（优先用Agent，LLM不可用时降级为直接正则分析）"""
    if log_agent is not None:
        try:
            return log_agent.analyze(log_text)
        except Exception as e:
            logger.warning(f"Agent分析异常，降级: {e}")

    # 降级：直接使用正则规则分析（不依赖LLM）
    return analyze_log_content.invoke({"log_text": log_text})


# ========== 知识库问答页 ==========

def kb_ask_question(question: str) -> str:
    """基于知识库回答运维问题"""
    if kb_agent is None:
        return "⚠️ 请先在顶部配置API密钥并初始化系统（知识库Agent需要加载嵌入模型）"

    if not question or not question.strip():
        return "⚠️ 请输入您想咨询的运维问题"

    return kb_agent.query(question)


# ========== 知识库管理页 ==========

def kb_import_document(file_obj) -> str:
    """导入文档到知识库"""
    if kb_agent is None:
        return "⚠️ 请先在顶部配置API密钥并初始化系统"

    if file_obj is None:
        return "⚠️ 请先选择要上传的文档（支持 .pdf / .docx / .txt）"

    file_path = file_obj.name
    logger.info(f"收到上传文档: {file_path}")
    return kb_agent.import_document(file_path)


def kb_list_docs() -> str:
    """列出知识库中的文档"""
    if kb_agent is None:
        return "⚠️ 请先在顶部配置API密钥并初始化系统"
    return kb_agent.list_documents()


def kb_delete_doc(doc_name: str) -> str:
    """删除指定文档"""
    if kb_agent is None:
        return "⚠️ 请先在顶部配置API密钥并初始化系统"

    if not doc_name or not doc_name.strip():
        return "⚠️ 请输入要删除的文档文件名"

    return kb_agent.delete_document(doc_name.strip())


def kb_get_stats() -> str:
    """获取知识库统计"""
    if kb_agent is None:
        return "⚠️ 请先在顶部配置API密钥并初始化系统"
    return kb_agent.get_stats()


def kb_clear_all() -> str:
    """清空知识库"""
    if kb_agent is None:
        return "⚠️ 请先在顶部配置API密钥并初始化系统"
    return kb_agent.clear_knowledge_base()


# ========== 构建Gradio界面 ==========

def create_ui():
    """创建完整的Gradio Web界面 — Modern SaaS style"""

    with gr.Blocks(
        title="OA 运维助手",
        css=CUSTOM_CSS,
    ) as app:

        # ===== Header =====
        gr.HTML("""
        <div class="app-header">
          <h1>OA 运维助手</h1>
          <p>巡检监控 · 日志分析 · 知识库问答</p>
        </div>
        """)

        # ===== Config Panel =====
        with gr.Accordion("设置", open=True):
            with gr.Row():
                mode_radio = gr.Radio(
                    choices=[
                        (MODE_PRESETS["local"]["label"], "local"),
                        (MODE_PRESETS["api"]["label"], "api"),
                    ],
                    value="local",
                    label="运行模式",
                    interactive=True,
                    scale=3,
                )
                init_btn = gr.Button("初始化系统", variant="primary", scale=1)
                mode_status = gr.Textbox(
                    label="状态",
                    value=get_mode_info(),
                    lines=1,
                    interactive=False,
                    scale=3,
                )
            with gr.Row():
                api_key_input = gr.Textbox(
                    label="API Key",
                    placeholder="离线模式无需填写",
                    value=MODE_PRESETS["local"]["api_key"],
                    type="password",
                    scale=3,
                    interactive=False,
                )
                api_base_input = gr.Textbox(
                    label="接口地址",
                    value=MODE_PRESETS["local"]["api_base"],
                    scale=2,
                    interactive=False,
                )
                api_model_input = gr.Textbox(
                    label="模型",
                    value=MODE_PRESETS["local"]["api_model"],
                    scale=2,
                    interactive=False,
                )
            init_status = gr.Textbox(label="初始化日志", value="就绪", lines=3)

            def on_mode_change(mode):
                preset = MODE_PRESETS[mode]
                return preset["api_key"], preset["api_base"], preset["api_model"], preset["label"]

            mode_radio.change(
                fn=on_mode_change,
                inputs=[mode_radio],
                outputs=[api_key_input, api_base_input, api_model_input, mode_status],
            )

            def do_init(mode, api_key, api_base, api_model):
                new_key, new_base, new_model, switch_msg = switch_mode(mode, api_key, api_base, api_model)
                mode_info = get_mode_info()
                for step_msg in init_agents(new_key, new_base, new_model):
                    yield new_key, new_base, new_model, mode_info, switch_msg + "\n" + step_msg

            init_btn.click(
                fn=do_init,
                inputs=[mode_radio, api_key_input, api_base_input, api_model_input],
                outputs=[api_key_input, api_base_input, api_model_input, mode_status, init_status],
            )

        # ===== Tabs =====
        with gr.Tabs():
            # ---- Tab 1: Inspection ----
            with gr.TabItem("巡检"):
                with gr.Row():
                    with gr.Column(scale=1):
                        interval_slider = gr.Slider(
                            minimum=10, maximum=300, value=30, step=10,
                            label="巡检间隔（秒）",
                        )
                        start_btn = gr.Button("启动定时巡检", variant="primary")
                        stop_btn = gr.Button("停止巡检", variant="stop")
                        adjust_btn = gr.Button("调整间隔", variant="secondary")
                        manual_btn = gr.Button("立即巡检", variant="secondary")

                    with gr.Column(scale=2):
                        scheduler_status = gr.Textbox(
                            label="调度器",
                            value=get_scheduler_status(),
                            lines=1,
                            elem_classes=["scheduler-status"],
                        )
                        inspection_output = gr.Textbox(
                            label="巡检结果",
                            placeholder="执行巡检后结果将显示在此处...",
                            lines=20,
                            max_lines=30,
                        )

                start_btn.click(
                    fn=start_inspection,
                    inputs=[interval_slider],
                    outputs=[inspection_output],
                ).then(fn=get_scheduler_status, outputs=[scheduler_status])

                stop_btn.click(
                    fn=stop_inspection,
                    outputs=[inspection_output],
                ).then(fn=get_scheduler_status, outputs=[scheduler_status])

                adjust_btn.click(
                    fn=adjust_interval,
                    inputs=[interval_slider],
                    outputs=[inspection_output],
                ).then(fn=get_scheduler_status, outputs=[scheduler_status])

                manual_btn.click(
                    fn=manual_inspection,
                    outputs=[inspection_output],
                )

                timer = gr.Timer(5)
                timer.tick(fn=get_scheduler_status, outputs=[scheduler_status])

                gr.Markdown("---")
                with gr.Row():
                    history_btn = gr.Button("巡检历史", variant="secondary", scale=1)
                    history_days = gr.Slider(1, 30, 7, step=1, label="天数", scale=1)
                    db_btn = gr.Button("数据库统计", variant="secondary", scale=1)
                history_output = gr.Textbox(
                    label="历史记录",
                    lines=12,
                    max_lines=20,
                )
                history_btn.click(
                    fn=get_inspection_history,
                    inputs=[history_days],
                    outputs=[history_output],
                )
                db_btn.click(
                    fn=get_db_overview,
                    outputs=[history_output],
                )

            # ---- Tab 2: Log Analysis ----
            with gr.TabItem("日志分析"):
                with gr.Row():
                    log_file_input = gr.File(
                        label="上传日志文件",
                        file_types=[".txt", ".log"],
                        scale=1,
                    )
                    log_text_input = gr.Textbox(
                        label="或粘贴日志内容",
                        placeholder="在此粘贴日志...",
                        lines=8,
                        scale=2,
                    )

                with gr.Row():
                    analyze_file_btn = gr.Button("分析文件", variant="primary")
                    analyze_text_btn = gr.Button("分析内容", variant="secondary")
                    clear_log_btn = gr.Button("清空", variant="stop")

                log_result = gr.Textbox(
                    label="分析报告",
                    placeholder="分析结果将显示在此处...",
                    lines=18,
                    max_lines=25,
                )

                analyze_file_btn.click(
                    fn=analyze_log_file,
                    inputs=[log_file_input],
                    outputs=[log_result],
                )
                analyze_text_btn.click(
                    fn=analyze_log_text,
                    inputs=[log_text_input],
                    outputs=[log_result],
                )
                clear_log_btn.click(
                    fn=lambda: ("", "", ""),
                    outputs=[log_file_input, log_text_input, log_result],
                )

            # ---- Tab 3: KB Q&A ----
            with gr.TabItem("知识问答"):
                with gr.Row():
                    kb_question_input = gr.Textbox(
                        label="运维问题",
                        placeholder="例如：OA 审批流程卡死了怎么办？",
                        lines=2,
                        scale=4,
                    )
                    kb_ask_btn = gr.Button("提问", variant="primary", scale=1)

                kb_answer_output = gr.Textbox(
                    label="回答",
                    placeholder="AI 将基于知识库为您解答...",
                    lines=16,
                    max_lines=25,
                )

                kb_ask_btn.click(
                    fn=kb_ask_question,
                    inputs=[kb_question_input],
                    outputs=[kb_answer_output],
                )

            # ---- Tab 4: KB Management ----
            with gr.TabItem("知识库"):
                with gr.Row():
                    with gr.Column(scale=1):
                        kb_upload_file = gr.File(
                            label="上传文档",
                            file_types=[".pdf", ".docx", ".txt"],
                        )
                        kb_import_btn = gr.Button("导入", variant="primary")
                        kb_stats_btn = gr.Button("统计", variant="secondary")

                    with gr.Column(scale=2):
                        kb_import_result = gr.Textbox(
                            label="导入结果",
                            placeholder="导入结果将显示在此处...",
                            lines=8,
                        )

                with gr.Row():
                    kb_list_btn = gr.Button("全部文档", variant="secondary")
                    kb_delete_name = gr.Textbox(
                        label="删除文档",
                        placeholder="输入文件名，如：运维手册.pdf",
                        scale=3,
                    )
                    kb_delete_btn = gr.Button("删除", variant="stop", scale=1)

                with gr.Row():
                    kb_clear_btn = gr.Button("清空知识库", variant="stop")

                kb_doc_list_output = gr.Textbox(
                    label="文档清单",
                    placeholder="点击「全部文档」查看...",
                    lines=12,
                )

                kb_import_btn.click(
                    fn=kb_import_document,
                    inputs=[kb_upload_file],
                    outputs=[kb_import_result],
                )
                kb_list_btn.click(
                    fn=kb_list_docs,
                    outputs=[kb_doc_list_output],
                )
                kb_delete_btn.click(
                    fn=kb_delete_doc,
                    inputs=[kb_delete_name],
                    outputs=[kb_doc_list_output],
                )
                kb_stats_btn.click(
                    fn=kb_get_stats,
                    outputs=[kb_doc_list_output],
                )
                kb_clear_btn.click(
                    fn=kb_clear_all,
                    outputs=[kb_doc_list_output],
                )

        # ===== Footer =====
        gr.HTML("""
        <div class="app-footer">
          OA 运维助手 &middot; LangChain + RAG &middot; 数据不出网
        </div>
        """)

    return app


# ========== 启动入口 ==========

if __name__ == "__main__":
    # 当直接运行此文件时启动Gradio服务
    app = create_ui()
    app.launch(
        server_name="0.0.0.0",   # 允许局域网访问
        server_port=7860,
        share=False,              # 设为True可生成公网链接
        inbrowser=True,           # 自动打开浏览器
        show_error=True,
    )
