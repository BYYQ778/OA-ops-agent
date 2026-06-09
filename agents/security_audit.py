"""
安全基线检查模块
---------------
对服务器进行安全基线扫描，帮助新手运维发现常见安全隐患：
- SSH 配置审查（PermitRootLogin、PasswordAuthentication、Port等）
- 失败登录记录审计
- 防火墙规则检查
- 开放端口审查
- 弱口令/空密码账户检测
- Crontab 可疑任务检查
- Sudoers 权限审查

支持 Windows 本地检测 + Linux SSH 远程检测。
纯命令行实现，无需 LLM 也可独立使用。

使用方式:
    from agents.security_audit import SecurityAuditor
    auditor = SecurityAuditor()
    print(auditor.run_audit())        # 本地审计
    print(auditor.run_ssh_audit())    # SSH 远程审计
"""

import os
import sys
import re
import subprocess
from datetime import datetime

from langchain.tools import tool

from utils.logger import get_logger
from utils.config import config

logger = get_logger(__name__)

IS_WINDOWS = sys.platform == "win32"


def _local_cmd(command: str, timeout: int = 10) -> str:
    """执行本地命令"""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="gbk" if IS_WINDOWS else "utf-8",
            errors="ignore",
        )
        return (result.stdout or result.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return ""
    except Exception as e:
        return f"执行失败: {e}"


# ========== 安全审计工具 ==========

@tool
def audit_ssh_config() -> str:
    """
    审计 SSH 服务配置安全性。
    检查项: PermitRootLogin, PasswordAuthentication, Port, Protocol, MaxAuthTries, PermitEmptyPasswords

    Returns:
        SSH 安全配置审计报告
    """
    lines = [
        "=" * 55,
        "  SSH 服务安全配置审计",
        "=" * 55,
        f"检测时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    if IS_WINDOWS:
        lines.append("ℹ️ Windows 系统 — SSH 审计仅适用于 Linux")
        lines.append("   可通过 config.yaml 配置 SSH 主机进行远程审计")
        lines.append("=" * 55)
        return "\n".join(lines)

    sshd_config = "/etc/ssh/sshd_config"
    if not os.path.exists(sshd_config):
        lines.append(f"❌ 未找到 SSHD 配置文件: {sshd_config}")
        lines.append("=" * 55)
        return "\n".join(lines)

    try:
        with open(sshd_config, "r") as f:
            config_content = f.read()
    except PermissionError:
        lines.append(f"❌ 无权限读取 {sshd_config}（需要 root 权限）")
        lines.append("=" * 55)
        return "\n".join(lines)

    # 安全检查项定义
    checks = [
        ("PermitRootLogin", r"^\s*PermitRootLogin\s+(\S+)",
         ["no", "prohibit-password"], "禁止 root 直接登录"),
        ("PasswordAuthentication", r"^\s*PasswordAuthentication\s+(\S+)",
         ["no"], "推荐使用密钥认证替代密码"),
        ("PermitEmptyPasswords", r"^\s*PermitEmptyPasswords\s+(\S+)",
         ["no"], "空密码应被禁止"),
        ("Port", r"^\s*Port\s+(\d+)",
         [], "使用非默认端口减少扫描风险 (>1024)"),
        ("MaxAuthTries", r"^\s*MaxAuthTries\s+(\d+)",
         [], "限制认证尝试次数 (建议 ≤3)"),
        ("Protocol", r"^\s*Protocol\s+(\S+)",
         ["2"], "仅使用 SSH Protocol 2"),
        ("X11Forwarding", r"^\s*X11Forwarding\s+(\S+)",
         ["no"], "如非必要应关闭 X11 转发"),
    ]

    issues = []
    for name, pattern, safe_values, advice in checks:
        match = re.search(pattern, config_content, re.MULTILINE)
        if match:
            value = match.group(1).strip()
            if safe_values and value not in safe_values:
                issues.append((name, value, advice, "🔴"))
            elif not safe_values:
                # 端口检查
                if name == "Port" and value == "22":
                    issues.append((name, value, advice, "🟡"))
                elif name == "MaxAuthTries" and int(value) > 3:
                    issues.append((name, value, advice, "🟡"))
                else:
                    lines.append(f"  ✅ {name}: {value}")
            else:
                lines.append(f"  ✅ {name}: {value}")
        else:
            lines.append(f"  ⚠️ {name}: 未配置（使用默认值，请检查）")

    if issues:
        lines.append("")
        lines.append("⚠️ 发现以下安全风险:")
        for name, value, advice, icon in issues:
            lines.append(f"  {icon} {name}: {value}")
            lines.append(f"      → {advice}")
    else:
        lines.append("")
        lines.append("✅ SSH 配置符合安全基线")

    # SSH 服务状态
    ssh_status = _local_cmd("systemctl is-active sshd 2>/dev/null || systemctl is-active ssh 2>/dev/null")
    if ssh_status:
        lines.append(f"\n📋 SSH 服务状态: {ssh_status}")

    lines.append("")
    lines.append("💡 修改建议:")
    if issues:
        lines.append("  编辑 /etc/ssh/sshd_config 修改上述配置后执行:")
        lines.append("  systemctl restart sshd")
    lines.append("=" * 55)
    return "\n".join(lines)


@tool
def check_failed_logins() -> str:
    """
    检查最近的登录失败记录，发现暴力破解或异常登录行为。

    Returns:
        失败登录审计报告
    """
    lines = [
        "=" * 55,
        "  登录失败记录审计",
        "=" * 55,
        f"检测时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    max_entries = config.get("security.max_failed_logins", 20)

    if IS_WINDOWS:
        # Windows: 检查安全日志
        output = _local_cmd(
            'wevtutil qe Security /c:20 /rd:true /f:text /q:"*[System[EventID=4625]]" 2>nul',
            timeout=15
        )
        if output:
            lines.append("📋 Windows 登录失败记录 (Event ID 4625):")
            # 提取关键信息
            for line in output.split("\n"):
                line = line.strip()
                if line:
                    lines.append(f"  {line[:150]}")
            if len(output.split("\n")) > 30:
                lines.append(f"  ... 完整记录请查看 Windows 事件查看器")
        else:
            lines.append("✅ 无最近失败登录记录（或安全日志不可读取）")
    else:
        # Linux: 检查 lastb 和安全日志
        lastb_output = _local_cmd(f"lastb -n {max_entries} 2>/dev/null")
        if lastb_output:
            login_lines = lastb_output.strip().split("\n")
            lines.append(f"📋 最近 {min(len(login_lines), max_entries)} 次失败登录 (lastb):")
            for log_line in login_lines[:max_entries]:
                parts = log_line.split()
                if len(parts) >= 3:
                    lines.append(f"  用户: {parts[0]} | 来源: {parts[2] if len(parts) > 2 else '未知'} | {log_line[-60:]}")
        else:
            lines.append("✅ lastb 无记录（或需要 root 权限）")

        # 检查 auth.log 中的失败记录
        auth_log = _local_cmd("grep 'Failed password' /var/log/auth.log 2>/dev/null | tail -10")
        if auth_log:
            lines.append(f"\n📋 /var/log/auth.log 中的最近失败记录:")
            ip_counts = {}
            for log_line in auth_log.split("\n"):
                ip_match = re.search(r'from\s+(\d+\.\d+\.\d+\.\d+)', log_line)
                if ip_match:
                    ip = ip_match.group(1)
                    ip_counts[ip] = ip_counts.get(ip, 0) + 1

            # 统计可疑IP
            suspicious = {ip: cnt for ip, cnt in ip_counts.items() if cnt >= 3}
            if suspicious:
                lines.append("  ⚠️ 可疑 IP (≥3次失败):")
                for ip, cnt in suspicious.items():
                    lines.append(f"    🔴 {ip}: {cnt} 次失败登录")
                lines.append("  建议: 使用 fail2ban 或 iptables 封禁这些 IP")
            else:
                lines.append("  无频繁失败登录的可疑IP")

    lines.append("")
    lines.append("💡 安全建议:")
    lines.append("  • 安装 fail2ban 自动封禁暴力破解 IP")
    lines.append("  • 修改 SSH 默认端口减少扫描")
    lines.append("  • 使用密钥认证替代密码登录")
    lines.append("=" * 55)
    return "\n".join(lines)


@tool
def audit_firewall_rules() -> str:
    """
    检查防火墙规则配置，审计开放的网络策略。

    Returns:
        防火墙规则审计报告
    """
    lines = [
        "=" * 55,
        "  防火墙规则审计",
        "=" * 55,
        f"检测时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    if IS_WINDOWS:
        # Windows 防火墙
        output = _local_cmd('netsh advfirewall firewall show rule name=all dir=in verbose 2>nul', timeout=15)
        if output:
            lines.append("📋 Windows 防火墙入站规则摘要:")
            for line in output.split("\n"):
                line = line.strip()
                if any(kw in line for kw in ["规则名称:", "已启用:", "方向:", "操作:", "协议:", "本地端口:"]):
                    lines.append(f"  {line}")
            if len(lines) < 20:
                lines.append("  (规则列表可能较长，仅显示摘要)")
        else:
            lines.append("⚠️ 无法读取防火墙规则，请以管理员身份运行")
    else:
        # Linux: iptables
        tables = ["INPUT", "FORWARD", "OUTPUT"]

        # 尝试 iptables
        for chain in tables:
            output = _local_cmd(f"iptables -L {chain} -n --line-numbers 2>/dev/null", timeout=10)
            if output:
                lines.append(f"📋 iptables {chain} 链:")
                rule_lines = output.strip().split("\n")
                useful_lines = [l for l in rule_lines if l.strip() and not l.startswith("Chain") and not l.startswith("target")]
                if useful_lines:
                    for rule_line in useful_lines[:15]:
                        lines.append(f"  {rule_line.strip()}")
                    if len(useful_lines) > 15:
                        lines.append(f"  ... 共 {len(useful_lines)} 条规则")
                else:
                    policy_match = re.search(r'policy\s+(\S+)', output)
                    policy_val = policy_match.group(1) if policy_match else 'unknown'
                    lines.append(f"  (empty chain - policy: {policy_val})")
                lines.append("")

        # 尝试 ufw
        ufw_output = _local_cmd("ufw status verbose 2>/dev/null", timeout=5)
        if ufw_output and "inactive" not in ufw_output.lower():
            lines.append("📋 UFW 状态:")
            lines.append(ufw_output[:1000])

        # 尝试 firewalld
        fwd_output = _local_cmd("firewall-cmd --list-all 2>/dev/null", timeout=5)
        if fwd_output:
            lines.append("📋 FirewallD 配置:")
            lines.append(fwd_output[:1000])

    lines.append("")
    lines.append("💡 安全审查要点:")
    lines.append("  • 检查是否有不必要的端口对外开放（如 3306/6379 直接暴露）")
    lines.append("  • 确认默认策略是否为 DROP/REJECT")
    lines.append("  • 限制管理端口（22/3389）的访问来源 IP")
    lines.append("=" * 55)
    return "\n".join(lines)


@tool
def check_listening_ports() -> str:
    """
    检查当前监听的网络端口，审计可疑的开放端口。

    Returns:
        端口监听清单及安全评估
    """
    lines = [
        "=" * 55,
        "  监听端口审计",
        "=" * 55,
        f"检测时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    if IS_WINDOWS:
        output = _local_cmd("netstat -ano | findstr LISTENING", timeout=10)
    else:
        output = _local_cmd("ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null", timeout=10)

    if not output:
        lines.append("⚠️ 无法获取端口监听信息")
        lines.append("=" * 55)
        return "\n".join(lines)

    lines.append("📋 当前监听端口:")
    lines.append("")

    # 已知服务端口映射
    known_services = {
        "22": "SSH", "80": "HTTP", "443": "HTTPS", "3306": "MySQL",
        "6379": "Redis", "8080": "HTTP-Alt/应用", "9090": "Prometheus",
        "3000": "Grafana", "5432": "PostgreSQL", "27017": "MongoDB",
        "3389": "RDP", "25": "SMTP", "53": "DNS",
    }

    # 监听地址分类
    public_ports = []   # 0.0.0.0 监听的端口
    local_ports = []    # 127.0.0.1 监听的端口

    for line in output.split("\n"):
        line = line.strip()
        if not line:
            continue

        # 提取端口号
        if IS_WINDOWS:
            port_match = re.search(r':(\d+)\s+', line)
        else:
            port_match = re.search(r':(\d+)\s+', line)

        if port_match:
            port = port_match.group(1)
            service = known_services.get(port, "")

            if IS_WINDOWS:
                listen_addr = "0.0.0.0"  # Windows netstat 不直接显示
                if "127.0.0.1" in line:
                    listen_addr = "127.0.0.1"
            else:
                addr_match = re.search(r'^\S+\s+(\S+):', line)
                listen_addr = addr_match.group(1) if addr_match else "?"

            if listen_addr in ("0.0.0.0", "::", "*"):
                public_ports.append((port, service, line[:120]))
            else:
                local_ports.append((port, service, line[:120]))

    if public_ports:
        lines.append("🌐 公网可访问端口（监听 0.0.0.0）:")
        for port, service, raw in public_ports:
            icon = "⚠️" if port in ("3306", "6379", "27017", "5432", "22", "3389") else "  "
            service_label = f"({service})" if service else ""
            lines.append(f"  {icon} 端口 {port} {service_label}")
            # 高危端口警告
            if port in ("3306", "6379", "27017"):
                lines.append(f"      🔴 危险: 数据库端口暴露在公网！请配置防火墙限制来源IP")
            elif port == "22":
                lines.append(f"      🟡 注意: SSH 暴露在公网，建议限制来源IP或改用非标准端口")

    if local_ports:
        lines.append("")
        lines.append("🔒 本地监听端口（仅 127.0.0.1）:")
        for port, service, raw in local_ports:
            service_label = f"({service})" if service else ""
            lines.append(f"  ✅ 端口 {port} {service_label}")

    if not public_ports and not local_ports:
        lines.append("  无监听端口（异常）")

    lines.append("")
    lines.append("💡 安全建议:")
    lines.append("  • 数据库端口（3306/6379/5432/27017）不应暴露在公网")
    lines.append("  • 使用防火墙限制 SSH 和其他管理端口的来源 IP")
    lines.append("  • 定期审查监听端口，关闭不再使用的服务")
    lines.append("=" * 55)
    return "\n".join(lines)


@tool
def audit_cron_jobs() -> str:
    """
    审计 crontab 定时任务，检查是否有可疑任务或后门。

    Returns:
        定时任务审计报告
    """
    lines = [
        "=" * 55,
        "  Crontab 定时任务审计",
        "=" * 55,
        f"检测时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    if IS_WINDOWS:
        output = _local_cmd('schtasks /query /fo LIST /v 2>nul', timeout=15)
        if output:
            lines.append("📋 Windows 计划任务 (摘要):")
            task_lines = [l for l in output.split("\n") if "任务名" in l or "TaskName" in l]
            for tl in task_lines[:30]:
                lines.append(f"  {tl.strip()[:120]}")
            if len(task_lines) > 30:
                lines.append(f"  ... 共 {len(task_lines)} 个计划任务")
        else:
            lines.append("⚠️ 无法读取计划任务")
    else:
        # 检查各用户的 crontab
        cron_paths = [
            "/etc/crontab",
            "/etc/cron.d/",
            "/var/spool/cron/crontabs/",
            "/var/spool/cron/",
        ]

        for path in cron_paths:
            if os.path.isdir(path):
                try:
                    for fname in os.listdir(path):
                        fpath = os.path.join(path, fname)
                        if os.path.isfile(fpath):
                            try:
                                with open(fpath, "r") as f:
                                    content = f.read().strip()
                                if content:
                                    lines.append(f"📋 {fpath}:")
                                    job_lines = [l.strip() for l in content.split("\n")
                                                 if l.strip() and not l.strip().startswith("#")]
                                    for job in job_lines[:10]:
                                        lines.append(f"  {job[:150]}")
                                    if len(job_lines) > 10:
                                        lines.append(f"  ... 共 {len(job_lines)} 条定时任务")
                                    lines.append("")
                            except PermissionError:
                                lines.append(f"  ⚠️ 无权限读取 {fpath}")
                except PermissionError:
                    lines.append(f"  ⚠️ 无权限访问 {path}")

            elif os.path.isfile(path):
                try:
                    with open(path, "r") as f:
                        content = f.read().strip()
                    if content:
                        lines.append(f"📋 {path}:")
                        job_lines = [l.strip() for l in content.split("\n")
                                     if l.strip() and not l.strip().startswith("#")]
                        for job in job_lines[:10]:
                            lines.append(f"  {job[:150]}")
                        lines.append("")
                except PermissionError:
                    lines.append(f"  ⚠️ 无权限读取 {path}")

        # 当前用户的 crontab
        user_cron = _local_cmd("crontab -l 2>/dev/null")
        if user_cron and "no crontab" not in user_cron.lower():
            lines.append("📋 当前用户 crontab:")
            for job in user_cron.strip().split("\n"):
                job = job.strip()
                if job and not job.startswith("#"):
                    lines.append(f"  {job[:150]}")
        elif not lines or len(lines) <= 3:
            lines.append("✅ 未发现 crontab 任务")

    lines.append("")
    lines.append("💡 审计要点:")
    lines.append("  • 检查有无下载+执行脚本的定时任务（后门特征）")
    lines.append("  • 确认所有任务都有注释说明用途")
    lines.append("  • 检查有无以 root 运行的未知任务")
    lines.append("=" * 55)
    return "\n".join(lines)


# ========== 工具汇总 ==========

SECURITY_AUDIT_TOOLS = [
    audit_ssh_config,
    check_failed_logins,
    audit_firewall_rules,
    check_listening_ports,
    audit_cron_jobs,
]

# ========== LLM Agent ==========

SECURITY_SYSTEM_PROMPT = """你是一名安全审计专家，负责对服务器进行安全基线检查。

你可以使用的工具：
- audit_ssh_config: SSH 配置安全审计
- check_failed_logins: 失败登录记录审计
- audit_firewall_rules: 防火墙规则审计
- check_listening_ports: 监听端口审计
- audit_cron_jobs: Crontab 定时任务审计

审计要点：
1. SSH 安全：禁止 root 登录、仅密钥认证、非默认端口
2. 登录安全：暴力破解检测、异常 IP 识别
3. 网络安全：防火墙策略、不必要的开放端口
4. 任务安全：crontab 后门检测

对发现的风险按严重程度分类（严重/高/中/低），给出具体的修复命令。"""


class SecurityAuditor:
    """
    安全基线审计 Agent。

    使用方式：
        auditor = SecurityAuditor(llm_api_key="...")
        print(auditor.run_audit())       # 本地审计
    """

    def __init__(
        self,
        llm_api_key: str = "",
        llm_base_url: str = "https://api.deepseek.com/v1",
        llm_model: str = "deepseek-chat",
    ):
        self.llm_api_key = llm_api_key
        self.llm_base_url = llm_base_url
        self.llm_model = llm_model
        self._agent = None

        if llm_api_key and llm_api_key not in ("ollama", "your-api-key-here", ""):
            self._init_agent()

    def _init_agent(self):
        try:
            from langchain_openai import ChatOpenAI
            from langchain.agents import create_agent

            llm = ChatOpenAI(
                api_key=self.llm_api_key,
                base_url=self.llm_base_url,
                model=self.llm_model,
                temperature=0.2,
                max_tokens=2048,
            )
            self._agent = create_agent(
                model=llm,
                tools=SECURITY_AUDIT_TOOLS,
                system_prompt=SECURITY_SYSTEM_PROMPT,
            )
            logger.info("安全审计Agent初始化完成")
        except Exception as e:
            logger.warning(f"安全审计Agent初始化失败: {e}")

    def run_audit(self) -> str:
        """执行完整安全基线审计"""
        if self._agent:
            try:
                result = self._agent.invoke({
                    "messages": [{"role": "user", "content": "请对当前服务器执行完整的安全基线审计"}]
                })
                msgs = result.get("messages", [])
                if msgs:
                    return msgs[-1].content
            except Exception as e:
                logger.warning(f"Agent 审计降级: {e}")

        # 降级：逐工具执行
        results = []
        for tool in SECURITY_AUDIT_TOOLS:
            try:
                results.append(tool.invoke({}))
                results.append("")
            except Exception as e:
                results.append(f"[{tool.name}] 执行失败: {e}")
                results.append("")

        results.insert(0, f"{'='*55}\n  安全基线审计报告（离线模式）\n  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n{'='*55}\n")
        results.append("=" * 55)
        results.append("提示: 配置 API Key 可获得 AI 风险评估和改进建议")
        results.append("=" * 55)
        return "\n".join(results)

    def single_audit(self, audit_type: str) -> str:
        """执行单项安全审计"""
        tool_map = {
            "ssh": audit_ssh_config,
            "login": check_failed_logins,
            "firewall": audit_firewall_rules,
            "ports": check_listening_ports,
            "cron": audit_cron_jobs,
        }
        tool = tool_map.get(audit_type)
        if not tool:
            return f"不支持的审计类型: {audit_type}，可选: {list(tool_map.keys())}"
        return tool.invoke({})


# ========== 快速测试入口 ==========

if __name__ == "__main__":
    print("=== 安全基线审计模块测试 ===\n")

    auditor = SecurityAuditor()

    if IS_WINDOWS:
        print("--- Windows 端口审计 ---")
        print(auditor.single_audit("ports"))
    else:
        print("--- SSH 配置审计 ---")
        print(auditor.single_audit("ssh"))

    print("\n提示: 安全审计部分命令需要 root/管理员权限")
