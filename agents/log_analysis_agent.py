"""
日志分析Agent模块
----------------
支持上传运维日志TXT文件，使用正则表达式自动匹配常见错误模式，
分类故障并给出标准化排查建议。

支持的故障类型：
- 502 Bad Gateway (Nginx上游服务不可达)
- 503 Service Unavailable (服务过载/宕机)
- 端口占用/冲突 (Address already in use)
- 流程卡死/超时 (Process stuck / timeout)
- OOM内存溢出 (Out of memory)
- 磁盘满 (No space left on device)
- 权限拒绝 (Permission denied)
- 数据库连接失败 (Connection refused / Access denied)
- 配置解析错误 (Config parse error)
- 文件句柄耗尽 (Too many open files)

设计思路：
- 每个故障类型对应一个正则规则集 + 排查建议
- 通过LangChain Agent统一分析并生成排查报告
- 支持直接上传TXT日志文件进行批量分析
"""

import re
import os
from datetime import datetime
from typing import List, Dict, Tuple

from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import ChatOpenAI

from utils.logger import get_logger

logger = get_logger(__name__)

# ========== 故障规则库 ==========
# 每条规则: (正则模式, 故障名称, 严重级别, 排查建议列表)
# 全部使用通用运维知识编写，不涉及任何公司特有信息

FAULT_RULES: List[Tuple[str, str, str, List[str]]] = [
    # ---- 502 Bad Gateway ----
    (
        r"502\s+Bad\s+Gateway",
        "502 Bad Gateway",
        "高",
        [
            "检查上游OA应用服务是否正常运行: ps aux | grep java",
            "检查应用端口是否正常监听: netstat -tlnp | grep 8080",
            "查看Nginx错误日志: tail -100 /var/log/nginx/error.log",
            "检查Nginx upstream配置中的服务器地址是否正确",
            "尝试直接curl上游服务地址，排除Nginx问题",
        ]
    ),
    (
        r"(upstream\s+timed\s+out|connect.*upstream.*timed\s+out)",
        "502 Bad Gateway（上游超时）",
        "高",
        [
            "增加Nginx upstream超时配置: proxy_read_timeout / proxy_connect_timeout",
            "检查OA应用是否存在慢查询或死锁",
            "检查数据库连接池是否耗尽",
            "查看应用GC日志，排查Full GC导致的暂停",
        ]
    ),
    # ---- 503 Service Unavailable ----
    (
        r"503\s+Service\s+(Unavailable|Temporarily\s+Unavailable)",
        "503 Service Unavailable",
        "严重",
        [
            "立即检查OA应用服务状态！",
            "查看应用日志定位报错: tail -500 catalina.out",
            "检查服务器资源: top / free -m / df -h",
            "检查是否有大量请求堆积: netstat -an | grep 8080 | wc -l",
            "考虑紧急重启应用服务（先保存现场日志）",
        ]
    ),
    # ---- 端口占用 ----
    (
        r"(Address\s+already\s+in\s+use|端口.*占用|bind.*failed.*address\s+already)",
        "端口占用/冲突",
        "中",
        [
            "查找占用端口的进程: lsof -i :端口号 或 netstat -tlnp | grep 端口号",
            "确认是否有僵尸进程未释放端口",
            "如果是合法进程占用，修改应用配置使用其他端口",
            "如果是异常占用，kill掉进程后重启服务",
        ]
    ),
    # ---- 流程卡死 ----
    (
        r"(流程.*卡死|process\s+stuck|task.*timeout|执行超时|deadlock|死锁)",
        "流程卡死/超时",
        "高",
        [
            "查看卡死流程的详细日志，定位卡在哪个步骤",
            "检查数据库是否存在锁等待: SHOW ENGINE INNODB STATUS",
            "检查外部API调用是否超时（如短信接口、邮件接口）",
            "确认流程的事务是否正常提交/回滚",
            "如确认无法恢复，清理卡死流程记录后重启相关服务",
        ]
    ),
    # ---- OOM ----
    (
        r"(Out\s+of\s+memory|OOM|out_of_memory|内存溢出|java\.lang\.OutOfMemoryError)",
        "OOM内存溢出",
        "严重",
        [
            "查看OOM时的堆栈信息，定位溢出原因（堆/元空间/直接内存）",
            "导出堆dump文件分析: jmap -dump:format=b,file=heap.hprof <pid>",
            "检查JVM启动参数中的内存配置: -Xmx -Xms",
            "检查是否存在内存泄漏，使用MAT或jhat分析dump文件",
            "短期方案: 增大JVM堆内存 -Xmx；长期方案: 修复内存泄漏代码",
        ]
    ),
    # ---- 磁盘满 ----
    (
        r"(No\s+space\s+left\s+on\s+device|磁盘空间不足|磁盘已满|disk\s+full)",
        "磁盘空间不足",
        "高",
        [
            "查看磁盘使用情况: df -h",
            "查找大文件: find / -type f -size +100M 2>/dev/null",
            "检查日志文件是否过大: du -sh /var/log/*",
            "清理过期日志、临时文件、core dump文件",
            "如为数据盘满，考虑扩容或归档历史数据",
        ]
    ),
    # ---- 权限拒绝 ----
    (
        r"(Permission\s+denied|权限不够|权限拒绝|Access\s+denied.*13)",
        "权限拒绝",
        "中",
        [
            "检查文件/目录权限: ls -la <path>",
            "确认运行服务的用户是否有读写权限",
            "检查SELinux是否拦截: getenforce ; ausearch -m avc",
            "检查文件所有者是否正确: chown / chmod",
            "检查umask设置是否合理",
        ]
    ),
    # ---- 数据库连接失败 ----
    (
        r"(Connection\s+refused.*database|数据库.*连接.*失败|CommunicationsException|Access\s+denied\s+for\s+user)",
        "数据库连接失败",
        "高",
        [
            "检查数据库服务是否运行: systemctl status mysql/mariadb",
            "检查数据库端口是否监听: netstat -tlnp | grep 3306",
            "检查防火墙规则: firewall-cmd --list-all",
            "验证数据库用户权限: SELECT user,host FROM mysql.user",
            "检查应用数据库连接配置（URL/用户名/密码）是否正确",
            "检查数据库最大连接数是否耗尽: SHOW VARIABLES LIKE 'max_connections'",
        ]
    ),
    # ---- 配置文件错误 ----
    (
        r"(config.*error|配置.*错误|syntax.*error.*config|YAML.*parse.*error|XML.*parse.*error)",
        "配置解析错误",
        "中",
        [
            "检查对应配置文件的语法是否正确",
            "验证配置项名称是否拼写正确",
            "检查XML/YAML缩进和标签闭合",
            "使用配置验证工具进行语法校验",
            "对比最近修改记录，回滚可疑变更",
        ]
    ),
    # ---- 文件句柄耗尽 ----
    (
        r"(Too\s+many\s+open\s+files|文件句柄.*不足|ulimit|EMFILE)",
        "文件句柄耗尽",
        "中",
        [
            "查看当前限制: ulimit -n",
            "查看进程使用的句柄数: lsof -p <pid> | wc -l",
            "临时增大限制: ulimit -n 65535",
            "永久修改: 编辑 /etc/security/limits.conf 添加 nofile 配置",
            "排查是否有文件句柄泄漏（打开未关闭的socket/文件）",
        ]
    ),
]


@tool
def analyze_log_content(log_text: str) -> str:
    """
    分析日志文本内容，使用正则表达式匹配常见故障模式，
    返回分类后的故障分析报告。

    Args:
        log_text: 待分析的日志文本内容（支持多行）

    Returns:
        结构化的故障分析报告
    """
    if not log_text or not log_text.strip():
        return "[提示] 日志内容为空，请上传有效的日志文件。"

    found_issues: List[Dict] = []  # 存储匹配到的问题
    matched_lines_set = set()      # 已匹配的行号，避免重复统计

    lines = log_text.split("\n")

    # 逐行扫描日志，匹配故障规则
    for line_num, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue

        for pattern, fault_name, severity, suggestions in FAULT_RULES:
            if re.search(pattern, line, re.IGNORECASE):
                found_issues.append({
                    "line": line_num,
                    "content": line[:200],  # 截取前200字符
                    "fault_name": fault_name,
                    "severity": severity,
                    "suggestions": suggestions,
                })
                matched_lines_set.add(line_num)
                break  # 一行只匹配一种故障类型

    # ---- 生成分析报告 ----
    if not found_issues:
        return (
            "[分析结果] 未发现已知的故障模式。\n"
            "建议: \n"
            "  1. 确认日志文件为OA系统相关日志\n"
            "  2. 检查日志时间范围是否覆盖故障发生时段\n"
            "  3. 如问题持续存在，可上传更完整的日志进行深度分析"
        )

    # 按严重级别统计
    severe_count = sum(1 for i in found_issues if i["severity"] == "严重")
    high_count = sum(1 for i in found_issues if i["severity"] == "高")
    medium_count = sum(1 for i in found_issues if i["severity"] == "中")

    report_lines = [
        "=" * 55,
        "         OA运维日志故障分析报告",
        "=" * 55,
        f"分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"扫描行数: {len(lines)}",
        f"发现故障: {len(found_issues)} 处",
        f"  严重: {severe_count} | 高: {high_count} | 中: {medium_count}",
        "=" * 55,
        "",
    ]

    # 按严重级别排序输出
    severity_order = {"严重": 0, "高": 1, "中": 2}
    found_issues.sort(key=lambda x: severity_order.get(x["severity"], 99))

    # 去重统计（同类故障合并）
    seen_faults = {}
    for issue in found_issues:
        key = issue["fault_name"]
        if key not in seen_faults:
            seen_faults[key] = {"issue": issue, "count": 0}
        seen_faults[key]["count"] += 1

    for idx, (fault_name, data) in enumerate(seen_faults.items(), start=1):
        issue = data["issue"]
        count = data["count"]
        severity_mark = {"严重": "!!", "高": "!!", "中": "!"}.get(issue["severity"], "")

        report_lines.append(f"--- 故障 {idx}: [{issue['severity']}] {fault_name} (出现 {count} 次) {severity_mark}")
        report_lines.append(f"    首次出现在第 {issue['line']} 行")
        report_lines.append(f"    日志摘要: {issue['content'][:120]}")
        report_lines.append(f"    排查建议:")
        for j, suggestion in enumerate(issue["suggestions"], start=1):
            report_lines.append(f"      {j}. {suggestion}")
        report_lines.append("")

    report_lines.append("=" * 55)
    report_lines.append("[总结]")
    if severe_count > 0:
        report_lines.append(f"  存在 {severe_count} 处严重故障，建议立即处理！")
    if high_count > 0:
        report_lines.append(f"  存在 {high_count} 处高风险故障，请尽快排查。")
    report_lines.append(f"  请按上述排查建议逐项处理，处理完成后验证服务是否恢复。")
    report_lines.append("=" * 55)

    return "\n".join(report_lines)


@tool
def scan_log_file(file_path: str) -> str:
    """
    读取本地日志文件并调用分析工具进行分析。
    支持大文件自动分块读取。

    Args:
        file_path: 日志文件的完整路径

    Returns:
        故障分析报告
    """
    if not os.path.exists(file_path):
        return f"[错误] 文件不存在: {file_path}"

    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)

    try:
        # 大文件(>50MB)分批读取
        if file_size_mb > 50:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                chunks = []
                while True:
                    chunk = f.read(1024 * 1024)  # 每次读1MB
                    if not chunk:
                        break
                    chunks.append(chunk)
                    if len(chunks) >= 100:  # 最多读100MB
                        break
                log_text = "\n".join(chunks)
        else:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                log_text = f.read()

        return analyze_log_content.invoke({"log_text": log_text})

    except Exception as e:
        return f"[错误] 读取文件失败: {str(e)}"


# ========== 汇总工具列表 ==========

LOG_ANALYSIS_TOOLS = [
    analyze_log_content,
    scan_log_file,
]

# ========== Agent 系统提示词 ==========

LOG_ANALYSIS_SYSTEM_PROMPT = """你是一名资深OA系统运维工程师，擅长分析系统日志并定位故障根因。

用户会提供OA系统日志内容（或日志文件路径），请按以下流程处理：

1. 调用 analyze_log_content 工具分析日志内容
2. 分析结果中会包含：
   - 匹配到的故障类型和严重级别
   - 故障发生的位置（行号）
   - 针对每种故障的标准化排查建议
3. 根据分析结果，向用户简要总结故障情况，并按优先级给出处理建议

注意：
- 如果日志中没有匹配到已知故障模式，告知用户并建议提供更完整的日志
- 对于"严重"级别的故障，语气应强调紧迫性
- 排查建议是标准化的，实际执行时需结合具体环境调整
"""


class LogAnalysisAgent:
    """
    日志分析Agent（基于LangChain AgentExecutor）。

    使用方式：
        agent = LogAnalysisAgent(llm_api_key="your-key", llm_base_url="...")
        report = agent.analyze(log_text="...")  # 分析日志文本
        report = agent.analyze_file("path/to/log")  # 分析日志文件
    """

    def __init__(
        self,
        llm_api_key: str = "your-api-key-here",
        llm_base_url: str = "https://api.deepseek.com/v1",
        llm_model: str = "deepseek-chat",
    ):
        """
        初始化日志分析Agent。

        Args:
            llm_api_key: 大模型API密钥
            llm_base_url: API地址
            llm_model: 模型名称
        """
        self.llm = ChatOpenAI(
            api_key=llm_api_key,
            base_url=llm_base_url,
            model=llm_model,
            temperature=0.2,
            max_tokens=2048,
        )

        self.agent = create_agent(
            model=self.llm,
            tools=LOG_ANALYSIS_TOOLS,
            system_prompt=LOG_ANALYSIS_SYSTEM_PROMPT,
        )

        logger.info("日志分析Agent初始化完成")

    def analyze(self, log_text: str) -> str:
        """
        分析日志文本，返回故障分析报告。

        Args:
            log_text: 日志文本内容

        Returns:
            分析报告字符串
        """
        if not log_text.strip():
            return "[提示] 请提供需要分析的日志内容。"

        logger.info(f"开始分析日志，文本长度: {len(log_text)} 字符")

        try:
            result = self.agent.invoke({
                "messages": [
                    {"role": "user", "content": f"请分析以下OA系统日志内容:\n\n{log_text[:8000]}"}
                ]
            })
            messages = result.get("messages", [])
            return messages[-1].content if messages else "分析未返回有效结果"
        except Exception as e:
            # LLM不可用时，降级为直接正则分析
            logger.warning(f"Agent分析异常，降级为直接正则分析: {e}")
            return analyze_log_content.invoke({"log_text": log_text})

    def analyze_file(self, file_path: str) -> str:
        """
        分析日志文件。

        Args:
            file_path: 日志文件路径

        Returns:
            分析报告字符串
        """
        logger.info(f"开始分析日志文件: {file_path}")
        return scan_log_file.invoke({"file_path": file_path})


# ========== 快速测试入口 ==========

if __name__ == "__main__":
    # 模拟一段包含多种故障的日志文本，测试正则规则匹配
    sample_log = """
2025-01-15 10:23:45 [ERROR] upstream timed out (110: Connection timed out) while connecting to upstream
2025-01-15 10:24:01 [ERROR] 502 Bad Gateway - /oa/approval/list
2025-01-15 10:25:12 [WARN]  Address already in use: bind 0.0.0.0:8080
2025-01-15 10:26:30 [ERROR] java.lang.OutOfMemoryError: Java heap space
2025-01-15 10:27:00 [FATAL] No space left on device - /data/logs/
2025-01-15 10:28:15 [ERROR] Connection refused: database 192.168.1.100:3306
2025-01-15 10:29:00 [ERROR] Task[APPROVAL_FLOW_001] execution timeout, process stuck
2025-01-15 10:30:00 [ERROR] Permission denied: /etc/nginx/conf.d/oa.conf
"""

    print("=== 正则规则直接测试 ===")
    result = analyze_log_content.invoke({"log_text": sample_log})
    print(result)
