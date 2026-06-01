"""
AI 巡检报告生成模块
------------------
将真实巡检结果发送给 LLM，自动生成：
- 预警分析（严重程度评估 + 风险等级）
- 改进策略（优先级排序 + 具体操作步骤）

使用方式:
    from agents.ai_reporter import generate_ai_report
    report = generate_ai_report(inspection_results)
"""

from datetime import datetime
from typing import List, Dict

from utils.config import config
from utils.logger import get_logger

logger = get_logger(__name__)

# ========== AI 报告 Prompt 模板 ==========

REPORT_SYSTEM_PROMPT = """你是一名资深运维架构师，负责对服务器巡检结果进行深度分析。

## 你的任务
根据提供的巡检数据，生成一份运维分析报告，包含：
1. 预警分析
2. 改进策略

## 报告格式要求

### 一、预警分析
- 按严重程度（严重 > 高 > 中）排序所有异常项
- 每项标注：问题描述、当前值 vs 阈值、风险等级(🔴严重/🟡警告/🟢正常)
- 评估如果不处理的潜在后果

### 二、改进策略
- 按优先级排序（立即处理 > 本周处理 > 计划处理）
- 每项策略包含：具体操作步骤（可直接执行）、预期效果、注意事项
- 优先给出可自动化的解决方案

## 分析原则
- 实事求是，基于数据说话
- 操作步骤必须具体、可直接执行
- 中文输出，技术术语保留英文
- 如果所有项正常，也要给出预防性建议"""


def generate_ai_report(inspection_results: List[Dict]) -> str:
    """
    将巡检结果发送 LLM，生成预警报告和改进策略。

    Args:
        inspection_results: 巡检结果列表
            [{"check_type_cn": "磁盘使用", "result": "...", "status": "warning"}, ...]

    Returns:
        AI 生成的格式化分析报告
    """
    api_key = config.get("llm.api_key", "")
    if not api_key:
        return _fallback_report(inspection_results)

    try:
        from langchain_openai import ChatOpenAI

        provider = config.get("llm.provider", "deepseek")

        # 根据 provider 选择参数
        if provider == "ollama":
            llm_base_url = config.get("llm.ollama.base_url", "http://localhost:11434/v1")
            llm_model = config.get("llm.ollama.model", "qwen2.5:7b")
            llm_api_key = "ollama"  # Ollama 不需要真 key
            llm_temp = 0.3
            llm_tokens = 2048
        else:
            llm_base_url = config.get("llm.base_url", "https://api.deepseek.com/v1")
            llm_model = config.get("llm.model", "deepseek-chat")
            llm_api_key = api_key
            llm_temp = 0.3
            llm_tokens = 2048

        logger.info(f"AI 报告引擎: {provider}/{llm_model}")

        llm = ChatOpenAI(
            api_key=llm_api_key,
            base_url=llm_base_url,
            model=llm_model,
            temperature=llm_temp,
            max_tokens=llm_tokens,
        )

        # 构建用户消息：把巡检数据喂给 LLM
        data_text = _format_inspection_data(inspection_results)
        user_message = f"""请分析以下服务器巡检数据，生成预警报告和改进策略。

巡检时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

{data_text}"""

        from langchain_core.messages import SystemMessage, HumanMessage
        response = llm.invoke([
            SystemMessage(content=REPORT_SYSTEM_PROMPT),
            HumanMessage(content=user_message),
        ])

        # 拼接完整报告
        engine_name = f"{provider}/{llm_model}" if provider != "ollama" else f"Ollama/{llm_model} (本地离线)"

        full_report = f"""\
{'='*55}
       OA系统巡检 AI 分析报告
{'='*55}
巡检时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
分析引擎: {engine_name}

{'─'*55}
{response.content}
{'─'*55}
注: 本报告由 AI 自动生成，仅供参考。关键操作请人工复核。
{'='*55}"""

        return full_report

    except Exception as e:
        logger.error(f"AI 报告生成失败: {e}")
        return _fallback_report(inspection_results)


def _format_inspection_data(results: List[Dict]) -> str:
    """将巡检结果格式化为 LLM 可读文本"""
    lines = []
    for r in results:
        lines.append(f"## {r.get('check_type_cn', '未知检测项')}")
        lines.append(f"状态: {r.get('status', '未知')}")
        lines.append(f"数据:")
        lines.append(r.get('result', '无数据'))
        lines.append("")
    return "\n".join(lines)


def _fallback_report(results: List[Dict]) -> str:
    """LLM 不可用时的降级报告"""
    errors = [r for r in results if r.get("status") == "error"]
    warnings = [r for r in results if r.get("status") == "warning"]
    normals = [r for r in results if r.get("status") == "normal"]

    lines = [
        "=" * 55,
        "       OA系统巡检报告（离线模式 - 无AI分析）",
        "=" * 55,
        f"巡检时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"📊 总计: {len(results)} 项检测",
        f"   ❌ 严重: {len(errors)} 项",
        f"   ⚠️ 告警: {len(warnings)} 项",
        f"   ✅ 正常: {len(normals)} 项",
        "",
        "─" * 30 + " 详细数据 " + "─" * 30,
        "",
    ]

    for r in results:
        lines.append(r.get("result", ""))
        lines.append("")

    lines.extend([
        "─" * 30 + " 改进建议 " + "─" * 30,
        "",
    ])

    if errors:
        lines.append("🔴 严重问题 - 建议立即处理:")
        for r in errors:
            lines.append(f"   • {r['check_type_cn']}: 请检查对应服务状态")
        lines.append("")

    if warnings:
        lines.append("🟡 告警问题 - 建议本周处理:")
        for r in warnings:
            lines.append(f"   • {r['check_type_cn']}: 关注趋势，必要时扩容或修复")
        lines.append("")

    if not errors and not warnings:
        lines.append("✅ 所有系统正常运行")
        lines.append("   • 建议: 保持当前监控频率，定期复查")
        lines.append("   • 建议: 建立基线数据，便于异常对比")

    lines.extend([
        "",
        "=" * 55,
        "提示: 配置 API Key 后可使用 AI 深度分析 → 编辑 .env 填 OA_LLM_API_KEY",
        "=" * 55,
    ])

    return "\n".join(lines)
