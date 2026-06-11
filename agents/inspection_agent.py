"""
巡检Agent模块
------------
模拟OA服务器巡检，使用LangChain Agent调度多个巡检工具。

检测项目：
- 关键端口状态（80/443/8080/3306/6379）
- Nginx服务状态
- OA应用服务状态
- 磁盘使用率
- 内存使用率

设计思路：
- 每个检测项封装为独立的LangChain Tool
- 通过AgentExecutor统一调度，支持自然语言交互
- 巡检结果写入本地日志文件
- 模拟数据使用随机数生成，部分概率出现异常告警
"""

import os
import json
import random
import time
from datetime import datetime
from typing import Optional

from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import ChatOpenAI

from utils.logger import get_logger
from utils.database import db

logger = get_logger(__name__)

# ========== 模拟服务器配置 ==========
# 全部使用模拟数据，不涉及任何真实IP或密钥

# 待检测的端口列表
MONITORED_PORTS = {
    80: "HTTP服务",
    443: "HTTPS服务",
    8080: "OA应用端口",
    3306: "MySQL数据库",
    6379: "Redis缓存",
}

# 磁盘挂载点（模拟）
DISK_MOUNTS = ["/dev/sda1 (/根目录)", "/dev/sdb1 (/data数据盘)"]

# ========== 巡检工具定义 ==========
# 每个@tool装饰的函数会注册为LangChain可调用的工具


@tool
def check_ports() -> str:
    """
    检测OA系统关键端口是否正常监听。
    返回各端口的连通性状态，如果端口不通则会标注异常。
    """
    results = []
    alert_ports = []

    for port, service_name in MONITORED_PORTS.items():
        # 模拟端口检测：90%概率正常，10%概率异常
        is_open = random.choices([True, False], weights=[90, 10])[0]

        if is_open:
            latency = round(random.uniform(0.3, 2.5), 2)
            results.append(f"  [正常] 端口 {port} ({service_name}): 监听中，延迟 {latency}ms")
        else:
            results.append(f"  [异常] 端口 {port} ({service_name}): 未监听！")
            alert_ports.append(str(port))

    status = "异常" if alert_ports else "正常"
    summary = f"端口检测完成，状态: {status}"
    if alert_ports:
        summary += f"，异常端口: {', '.join(alert_ports)}"

    return summary + "\n" + "\n".join(results)


@tool
def check_nginx_status() -> str:
    """
    检测Nginx反向代理服务运行状态。
    模拟Nginx进程检查、配置文件语法检查。
    """
    # 模拟：85%正常运行，10%进程不存在，5%配置异常
    status_roll = random.choices(
        ["running", "stopped", "config_error"],
        weights=[85, 10, 5]
    )[0]

    if status_roll == "running":
        uptime_hours = random.randint(1, 720)  # 1小时到30天
        active_conn = random.randint(50, 500)
        return (
            f"[正常] Nginx服务运行中\n"
            f"  运行时长: {uptime_hours}小时\n"
            f"  活跃连接数: {active_conn}\n"
            f"  配置文件语法: OK"
        )
    elif status_roll == "stopped":
        return (
            f"[异常] Nginx服务已停止！\n"
            f"  建议: 执行 systemctl start nginx 启动服务\n"
            f"  建议: 检查 /var/log/nginx/error.log 排查原因"
        )
    else:
        return (
            f"[异常] Nginx配置文件语法错误！\n"
            f"  建议: 执行 nginx -t 检查配置文件\n"
            f"  常见原因: 缺少分号、花括号不匹配、include路径错误"
        )


@tool
def check_oa_service() -> str:
    """
    检测OA应用服务运行状态。
    模拟Tomcat/Java进程检查、应用响应检测。
    """
    # 模拟：80%正常，15%响应慢，5%宕机
    status_roll = random.choices(
        ["normal", "slow", "down"],
        weights=[80, 15, 5]
    )[0]

    if status_roll == "normal":
        response_time = round(random.uniform(100, 500), 0)
        return (
            f"[正常] OA应用服务运行正常\n"
            f"  HTTP响应时间: {response_time}ms\n"
            f"  并发用户数: {random.randint(10, 80)}\n"
            f"  会话数: {random.randint(20, 200)}"
        )
    elif status_roll == "slow":
        response_time = round(random.uniform(3000, 8000), 0)
        return (
            f"[告警] OA应用服务响应缓慢！\n"
            f"  HTTP响应时间: {response_time}ms (超过阈值2000ms)\n"
            f"  JVM堆内存使用率: {random.randint(85, 98)}%\n"
            f"  建议: 检查数据库连接池、JVM GC日志、慢SQL"
        )
    else:
        return (
            f"[严重] OA应用服务疑似宕机！\n"
            f"  HTTP 503 Service Unavailable\n"
            f"  建议: 立即检查应用日志 catalina.out\n"
            f"  建议: 检查进程是否存在 ps aux | grep tomcat\n"
            f"  建议: 尝试重启服务"
        )


@tool
def check_disk_usage() -> str:
    """
    检测服务器磁盘使用率。
    模拟df -h命令输出，对超过阈值的使用率告警。
    """
    results = []
    has_alert = False
    alert_threshold = 85  # 磁盘使用率告警阈值

    for mount in DISK_MOUNTS:
        # 模拟磁盘使用率：大部分情况在30-70%，小概率超过阈值
        if random.random() < 0.15:  # 15%概率触发磁盘告警
            usage = random.randint(alert_threshold, 98)
            has_alert = True
            results.append(f"  [告警] {mount}: 使用率 {usage}% (超过阈值{alert_threshold}%)")
        else:
            usage = random.randint(20, alert_threshold - 5)
            results.append(f"  [正常] {mount}: 使用率 {usage}%")

    summary = "磁盘检测完成"
    if has_alert:
        summary += " —— 存在磁盘告警，请及时清理或扩容！"

    return summary + "\n" + "\n".join(results)


@tool
def check_memory_usage() -> str:
    """
    检测服务器内存使用率。
    模拟free -m命令输出。
    """
    total_gb = random.choice([16, 32, 64])  # 模拟内存总量

    if random.random() < 0.12:  # 12%概率内存使用率过高
        used_percent = random.randint(88, 97)
        available_gb = round(total_gb * (100 - used_percent) / 100, 1)
        return (
            f"[告警] 内存使用率过高！\n"
            f"  总内存: {total_gb}GB\n"
            f"  使用率: {used_percent}%\n"
            f"  可用内存: {available_gb}GB\n"
            f"  建议: 排查内存泄漏进程 top -o %MEM\n"
            f"  建议: 检查是否有OOM Killer事件 dmesg | grep -i oom"
        )
    else:
        used_percent = random.randint(30, 78)
        available_gb = round(total_gb * (100 - used_percent) / 100, 1)
        return (
            f"[正常] 内存使用正常\n"
            f"  总内存: {total_gb}GB\n"
            f"  使用率: {used_percent}%\n"
            f"  可用内存: {available_gb}GB"
        )


# ========== 汇总所有工具 ==========

INSPECTION_TOOLS = [
    check_ports,
    check_nginx_status,
    check_oa_service,
    check_disk_usage,
    check_memory_usage,
]


# ========== Agent 系统提示词 ==========

INSPECTION_SYSTEM_PROMPT = """你是一名资深OA系统运维工程师，负责对OA服务器进行自动化巡检。

你的巡检流程：
1. 依次调用以下工具执行检测：
   - check_ports：检测关键端口状态
   - check_nginx_status：检测Nginx服务
   - check_oa_service：检测OA应用服务
   - check_disk_usage：检测磁盘使用率
   - check_memory_usage：检测内存使用率

2. 全部检测完成后，汇总生成巡检报告，格式如下：
   ========================================
   OA系统巡检报告
   巡检时间: {当前时间}
   ========================================
   [逐项列出检测结果，标注正常/告警/异常]
   ========================================
   总结: [简要总结，如存在异常则给出优先级建议]

请务必调用全部5个工具后再生成报告。"""


class InspectionAgent:
    """
    OA巡检Agent（基于LangChain AgentExecutor）。

    使用方式：
        agent = InspectionAgent(llm_api_key="your-key", llm_base_url="...")
        result = agent.run_inspection()  # 执行一次完整巡检
    """

    def __init__(
        self,
        llm_api_key: str = "your-api-key-here",
        llm_base_url: str = "https://api.deepseek.com/v1",
        llm_model: str = "deepseek-chat",
    ):
        """
        初始化巡检Agent。

        Args:
            llm_api_key: 大模型API密钥（DeepSeek/千问等，兼容OpenAI格式）
            llm_base_url: API地址
            llm_model: 模型名称
        """
        self.llm_api_key = llm_api_key
        self.llm_base_url = llm_base_url
        self.llm_model = llm_model

        # 初始化大模型实例
        self.llm = ChatOpenAI(
            api_key=llm_api_key,
            base_url=llm_base_url,
            model=llm_model,
            temperature=0.3,    # 低温度保证输出稳定
            max_tokens=2048,
        )

        self.agent = create_agent(
            model=self.llm,
            tools=INSPECTION_TOOLS,
            system_prompt=INSPECTION_SYSTEM_PROMPT,
        )

        logger.info("巡检Agent初始化完成")

    def run_inspection(self) -> str:
        """
        执行一次完整的OA系统巡检，返回巡检报告文本。

        Returns:
            格式化后的巡检报告字符串
        """
        start_time = time.time()
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        logger.info(f"开始执行巡检...")

        try:
            result = self.agent.invoke({
                "messages": [
                    {"role": "user", "content": f"请执行完整的OA系统巡检，当前时间: {current_time}"}
                ]
            })
            messages = result.get("messages", [])
            output = messages[-1].content if messages else "巡检未返回有效结果"
            elapsed = time.time() - start_time
            logger.info(f"巡检完成，耗时: {elapsed:.2f}秒")

            # 将巡检结果追加写入日志文件
            self._save_to_log(current_time, output)

            return output

        except Exception as e:
            error_msg = f"巡检执行异常: {str(e)}"
            logger.error(error_msg)
            return error_msg

    def run_single_check(self, check_name: str) -> str:
        """
        执行单项检测（用于快速诊断）。

        Args:
            check_name: 检测项名称，支持: ports/nginx/oa/disk/memory

        Returns:
            单项检测结果字符串
        """
        tool_map = {
            "ports": check_ports,
            "nginx": check_nginx_status,
            "oa": check_oa_service,
            "disk": check_disk_usage,
            "memory": check_memory_usage,
        }

        if check_name not in tool_map:
            return f"不支持的检测项: {check_name}，可选: {list(tool_map.keys())}"

        try:
            result = tool_map[check_name].invoke({})
            return result
        except Exception as e:
            return f"检测失败: {str(e)}"

    def _save_to_log(self, timestamp: str, report: str):
        """
        将巡检报告持久化到本地日志文件。

        Args:
            timestamp: 巡检时间戳
            report: 巡检报告内容
        """
        log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "inspection_logs")
        os.makedirs(log_dir, exist_ok=True)

        log_file = os.path.join(log_dir, f"inspection_{datetime.now().strftime('%Y%m%d')}.log")

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"巡检时间: {timestamp}\n")
            f.write(f"{'='*60}\n")
            f.write(report)
            f.write(f"\n{'='*60}\n")


# ========== 巡检结果解析与持久化 ==========

def _parse_inspection_status(result_text: str) -> str:
    """从巡检结果文本中提取状态"""
    if "[严重]" in result_text or "[异常]" in result_text:
        return "error"
    elif "[告警]" in result_text:
        return "warning"
    return "normal"


def save_inspection_to_db(results: list):
    """
    将巡检结果批量存入数据库。

    Args:
        results: [{"check_type": "ports", "check_type_cn": "端口检测",
                   "target": "", "result": "...", "is_simulated": True}, ...]
    """
    for r in results:
        r["status"] = _parse_inspection_status(r.get("result", ""))
    try:
        db.save_inspection_batch(results)
    except Exception as e:
        logger.warning(f"巡检记录入库失败: {e}")


# ========== 统一巡检入口（消除 main.py 与 ui/server.py 重复代码）==========

def run_unified_inspection() -> str:
    """
    执行完整巡检流程：SSH/local → 模拟降级 → 入库 → AI 报告。

    所有调用方（main.py CLI模式、ui/server.py Web模式）统一使用此函数，
    避免巡检逻辑在多处重复维护。

    Returns:
        格式化后的巡检报告字符串
    """
    from datetime import datetime as dt
    from utils.config import config as cfg

    mode = cfg.get("inspection.mode", "simulated")
    report_lines: list[str] = []
    report_lines.append("=" * 55)
    db_records: list[dict] = []

    # ---- 尝试真实巡检（SSH 或 本机）----
    if mode in ("ssh", "auto", "local"):
        try:
            from agents.inspection_real import run_ssh_inspection
            ssh_result = run_ssh_inspection()

            if ssh_result["mode"] == "ssh" and ssh_result["results"]:
                report_lines.append("OA系统巡检报告（SSH 真实检测）")
                report_lines.append(f"巡检时间: {dt.now().strftime('%Y-%m-%d %H:%M:%S')}")
                report_lines.append("巡检模式: SSH 真实服务器")
                report_lines.append("=" * 55)
                report_lines.append("")
                for r in ssh_result["results"]:
                    report_lines.append(r["result"])
                    report_lines.append("")
                    db_records.append(r)
                try:
                    save_inspection_to_db(db_records)
                except Exception:
                    pass
                report_lines.append("=" * 55)
                report_lines.append("提示: SSH 真实巡检完成，数据来自远程服务器")
                ai_report = _try_ai_report(db_records)
                if ai_report:
                    report_lines.append("")
                    report_lines.append(ai_report)
                return "\n".join(report_lines)

            elif ssh_result["mode"] == "local" and ssh_result["results"]:
                report_lines.append("OA系统巡检报告（本机真实检测）")
                report_lines.append(f"巡检时间: {dt.now().strftime('%Y-%m-%d %H:%M:%S')}")
                report_lines.append("巡检模式: 本机 Windows 命令检测")
                report_lines.append("=" * 55)
                report_lines.append("")
                for r in ssh_result["results"]:
                    report_lines.append(r["result"])
                    report_lines.append("")
                    db_records.append(r)
                try:
                    save_inspection_to_db(db_records)
                except Exception:
                    pass
                report_lines.append("=" * 55)
                report_lines.append(f"  共 {len(ssh_result['results'])} 项检测完成")
                ai_report = _try_ai_report(db_records)
                if ai_report:
                    report_lines.append("")
                    report_lines.append(ai_report)
                return "\n".join(report_lines)

            elif ssh_result["mode"] == "ssh" and not ssh_result.get("success"):
                report_lines.append("OA系统巡检报告（SSH 连接失败）")
                report_lines.append(f"巡检时间: {dt.now().strftime('%Y-%m-%d %H:%M:%S')}")
                report_lines.append("=" * 55)
                report_lines.append("")
                report_lines.append(f"  {ssh_result.get('error', 'SSH 连接失败')}")
                report_lines.append("")
                report_lines.append("排查建议:")
                report_lines.append("  1. 检查 config.yaml → inspection.ssh_hosts 配置")
                report_lines.append("  2. 确认目标服务器 SSH 端口可达")
                report_lines.append("  3. 验证密码/密钥是否正确")
                report_lines.append("  4. 如需使用模拟模式: 修改 inspection.mode 为 simulated")
                return "\n".join(report_lines)

        except ImportError:
            if mode == "ssh":
                return "SSH 巡检失败: paramiko 未安装。请执行 pip install paramiko"
        except Exception as e:
            logger.warning(f"真实巡检异常: {e}")
            if mode == "ssh":
                return f"SSH 巡检失败: {e}"

    # ---- 降级：模拟巡检 ----
    if mode == "auto":
        report_lines.append("OA系统巡检报告（自动降级 - 模拟数据）")
        report_lines.append(f"巡检时间: {dt.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report_lines.append("巡检模式: SSH 不可用，已自动降级为模拟")
    elif mode == "simulated":
        report_lines.append("OA系统巡检报告（模拟数据）")
        report_lines.append(f"巡检时间: {dt.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report_lines.append("巡检模式: 模拟数据")
    else:
        report_lines.append("OA系统巡检报告（降级模式）")
        report_lines.append(f"巡检时间: {dt.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report_lines.append("=" * 55)
    report_lines.append("")

    tools = [
        (check_ports, "端口检测", "ports"),
        (check_nginx_status, "Nginx服务", "nginx"),
        (check_oa_service, "OA应用服务", "oa"),
        (check_disk_usage, "磁盘使用", "disk"),
        (check_memory_usage, "内存使用", "memory"),
    ]

    for tool_func, cn_name, type_id in tools:
        try:
            result = tool_func.invoke({})
            report_lines.append(result)
            report_lines.append("")
            db_records.append({
                "check_type": type_id,
                "check_type_cn": cn_name,
                "target": "",
                "result": result,
                "is_simulated": True,
            })
        except Exception as e:
            error_text = f"[错误] {tool_func.name}: {e}"
            report_lines.append(error_text)
            report_lines.append("")
            db_records.append({
                "check_type": type_id,
                "check_type_cn": cn_name,
                "target": "",
                "result": error_text,
                "is_simulated": True,
            })

    try:
        save_inspection_to_db(db_records)
    except Exception:
        pass

    report_lines.append("=" * 55)
    if mode == "auto":
        report_lines.append("提示: SSH 不可用，已自动使用模拟数据。可改为 local 使用本机检测。")
    else:
        report_lines.append("提示: 模拟模式使用随机数据。配置 SSH 或切换 local 模式启用真实检测。")

    ai_report = _try_ai_report(db_records)
    if ai_report:
        report_lines.append("")
        report_lines.append(ai_report)

    return "\n".join(report_lines)


def _try_ai_report(db_records: list) -> str:
    """尝试生成AI分析报告，失败返回空字符串"""
    try:
        from agents.ai_reporter import generate_ai_report
        return generate_ai_report(db_records)
    except Exception:
        return ""


# ========== 快速测试入口 ==========

if __name__ == "__main__":
    # 本地测试：直接调用工具函数（不依赖大模型API）
    print("=== 端口检测 ===")
    print(check_ports.invoke({}))
    print("\n=== Nginx检测 ===")
    print(check_nginx_status.invoke({}))
    print("\n=== OA服务检测 ===")
    print(check_oa_service.invoke({}))
    print("\n=== 磁盘检测 ===")
    print(check_disk_usage.invoke({}))
    print("\n=== 内存检测 ===")
    print(check_memory_usage.invoke({}))
