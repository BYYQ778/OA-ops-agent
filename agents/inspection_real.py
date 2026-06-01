"""
SSH 真实巡检模块
---------------
通过 SSH 连接远程服务器，执行真实系统检测命令，替代模拟随机数据。

支持三种模式：
- simulated: 使用随机模拟数据（默认，适合演示）
- ssh: 通过 paramiko SSH 连接真实服务器
- auto: 先尝试 SSH，失败则自动降级为模拟模式

SSH 连接支持：
- 密码认证
- 私钥认证（优先）
- 多台服务器同时巡检

使用方式：
    from agents.inspection_real import RealInspector
    inspector = RealInspector(host_config)
    result = inspector.check_ports()       # 真实端口检测
    result = inspector.run_full_inspection()  # 完整巡检
"""

import os
import time
from datetime import datetime
from typing import List, Dict, Optional, Tuple

from utils.config import config
from utils.logger import get_logger

logger = get_logger(__name__)

# ========== SSH 检测命令定义 ==========

# 每种检测对应的远程命令
INSPECTION_COMMANDS = {
    "ports": {
        "name_cn": "端口检测",
        "check_type": "ports",
        "command": (
            "echo '=== PORTS ==='; "
            "for port in 80 443 8080 3306 6379; do "
            "  if ss -tlnp 2>/dev/null | grep -q \":$port \"; then "
            "    echo \"PORT_OK:$port\"; "
            "  else "
            "    echo \"PORT_DOWN:$port\"; "
            "  fi; "
            "done"
        ),
        "timeout": 15,
    },
    "nginx": {
        "name_cn": "Nginx服务",
        "check_type": "nginx",
        "command": (
            "echo '=== NGINX ==='; "
            "systemctl is-active nginx 2>/dev/null || service nginx status 2>/dev/null | grep -q 'is running' && echo 'NGINX_RUNNING' || echo 'NGINX_STOPPED'; "
            "nginx -t 2>&1 | tail -1; "
            "ss -tlnp 2>/dev/null | grep nginx | wc -l | xargs -I{} echo 'NGINX_CONN:{}'"
        ),
        "timeout": 15,
    },
    "oa_service": {
        "name_cn": "OA应用服务",
        "check_type": "oa",
        "command": (
            "echo '=== OA_SERVICE ==='; "
            "TOMCAT_PID=$(ps aux 2>/dev/null | grep -i '[t]omcat\|[j]ava.*oa\|[j]ava.*catalina' | awk '{print $2}' | head -1); "
            "if [ -n \"$TOMCAT_PID\" ]; then "
            "  echo 'OA_RUNNING'; "
            "  echo \"PID:$TOMCAT_PID\"; "
            "  ps -p $TOMCAT_PID -o etime= 2>/dev/null | xargs echo 'UPTIME:'; "
            "  ss -tlnp 2>/dev/null | grep $TOMCAT_PID | wc -l | xargs -I{} echo 'LISTEN_PORTS:{}'; "
            "else "
            "  echo 'OA_STOPPED'; "
            "fi"
        ),
        "timeout": 15,
    },
    "disk": {
        "name_cn": "磁盘使用",
        "check_type": "disk",
        "command": (
            "echo '=== DISK ==='; "
            "df -h --output=target,pcent,size,avail 2>/dev/null | tail -n +2 || "
            "df -h 2>/dev/null | awk '{print $NF, $5, $2, $4}' | tail -n +2"
        ),
        "timeout": 10,
    },
    "memory": {
        "name_cn": "内存使用",
        "check_type": "memory",
        "command": (
            "echo '=== MEMORY ==='; "
            "free -m 2>/dev/null | awk 'NR==2{printf \"MEM_TOTAL:%s MEM_USED:%s MEM_FREE:%s MEM_PCT:%.1f\", $2, $3, $4, $3*100/$2}'"
        ),
        "timeout": 10,
    },
}


class RealInspector:
    """
    SSH 真实巡检器。

    使用方式：
        hosts = config.get("inspection.ssh_hosts", [])
        inspector = RealInspector(hosts[0])  # 巡检第一台服务器
        result = inspector.check_ports()
    """

    def __init__(self, host_config: dict):
        """
        Args:
            host_config: {"name": "OA服务器1", "host": "192.168.1.100",
                         "port": 22, "user": "root", "password": "...",
                         "private_key_path": "..."}
        """
        self.name = host_config.get("name", "未知服务器")
        self.host = host_config.get("host", "")
        self.port = host_config.get("port", 22)
        self.user = host_config.get("user", "root")
        self.password = host_config.get("password", "")
        self.key_path = host_config.get("private_key_path", "")
        self._client = None
        self._connected = False

    def connect(self) -> bool:
        """建立 SSH 连接"""
        if self._connected:
            return True

        if not self.host:
            logger.warning(f"[{self.name}] 未配置主机地址")
            return False

        try:
            import paramiko
            self._client = paramiko.SSHClient()
            self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            connect_kwargs = {
                "hostname": self.host,
                "port": self.port,
                "username": self.user,
                "timeout": 10,
                "banner_timeout": 10,
            }

            # 优先使用私钥认证
            if self.key_path and os.path.exists(self.key_path):
                key = paramiko.RSAKey.from_private_key_file(self.key_path)
                connect_kwargs["pkey"] = key
            elif self.password:
                connect_kwargs["password"] = self.password
            else:
                logger.warning(f"[{self.name}] 未配置认证方式（密码或私钥）")
                return False

            self._client.connect(**connect_kwargs)
            self._connected = True
            logger.info(f"[{self.name}] SSH 连接成功: {self.user}@{self.host}:{self.port}")
            return True

        except ImportError:
            logger.error("paramiko 未安装，无法使用 SSH 巡检")
            return False
        except Exception as e:
            logger.warning(f"[{self.name}] SSH 连接失败: {e}")
            return False

    def disconnect(self):
        """断开 SSH 连接"""
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
            self._connected = False

    def _exec_command(self, command: str, timeout: int = 15) -> Tuple[str, str]:
        """执行远程命令，返回 (stdout, stderr)"""
        if not self._connected and not self.connect():
            return "", "SSH 未连接"

        try:
            stdin, stdout, stderr = self._client.exec_command(command, timeout=timeout)
            out = stdout.read().decode("utf-8", errors="ignore").strip()
            err = stderr.read().decode("utf-8", errors="ignore").strip()
            return out, err
        except Exception as e:
            return "", str(e)

    # ========== 真实检测方法 ==========

    def check_ports(self) -> str:
        """真实端口检测"""
        cmd = INSPECTION_COMMANDS["ports"]
        out, err = self._exec_command(cmd["command"], cmd["timeout"])

        if err and not out:
            return f"[{self.name}] 端口检测失败: {err}"

        lines = []
        ports_status = {}
        for line in out.split("\n"):
            if line.startswith("PORT_OK:"):
                port = line.split(":")[1]
                ports_status[port] = "normal"
            elif line.startswith("PORT_DOWN:"):
                port = line.split(":")[1]
                ports_status[port] = "error"

        # 生成报告
        SERVICE_NAMES = {"80": "HTTP", "443": "HTTPS", "8080": "OA应用", "3306": "MySQL", "6379": "Redis"}

        for port in ["80", "443", "8080", "3306", "6379"]:
            status = ports_status.get(port, "unknown")
            name = SERVICE_NAMES.get(port, "")
            if status == "normal":
                lines.append(f"  [正常] 端口 {port} ({name}): 监听中")
            elif status == "error":
                lines.append(f"  [异常] 端口 {port} ({name}): 未监听！")
            else:
                lines.append(f"  [未知] 端口 {port} ({name}): 无法检测")

        alert_ports = [p for p, s in ports_status.items() if s == "error"]
        status = "异常" if alert_ports else "正常"
        summary = f"[{self.name}] 端口检测完成，状态: {status}"
        if alert_ports:
            summary += f"，异常端口: {', '.join(alert_ports)}"

        return summary + "\n" + "\n".join(lines)

    def check_nginx(self) -> str:
        """真实 Nginx 检测"""
        cmd = INSPECTION_COMMANDS["nginx"]
        out, err = self._exec_command(cmd["command"], cmd["timeout"])

        if err and not out:
            return f"[{self.name}] Nginx检测失败: {err}"

        lines = [f"[{self.name}] Nginx服务检测:"]

        if "NGINX_RUNNING" in out:
            lines.append("  [正常] Nginx服务运行中")
        elif "NGINX_STOPPED" in out:
            lines.append("  [异常] Nginx服务已停止！")
            lines.append("  建议: 执行 systemctl start nginx 启动服务")

        # 提取配置检查结果
        for line in out.split("\n"):
            if "syntax is ok" in line.lower():
                lines.append(f"  配置文件: {line.strip()}")
            elif "test failed" in line.lower() or "emerg" in line.lower():
                lines.append(f"  [异常] 配置错误: {line.strip()}")

        # 提取连接数
        for line in out.split("\n"):
            if line.startswith("NGINX_CONN:"):
                conn = line.split(":")[1].strip()
                lines.append(f"  监听端口数: {conn}")

        return "\n".join(lines)

    def check_oa_service(self) -> str:
        """真实 OA 服务检测"""
        cmd = INSPECTION_COMMANDS["oa_service"]
        out, err = self._exec_command(cmd["command"], cmd["timeout"])

        if err and not out:
            return f"[{self.name}] OA服务检测失败: {err}"

        lines = [f"[{self.name}] OA应用服务检测:"]

        if "OA_RUNNING" in out:
            lines.append("  [正常] OA应用服务运行中")
            for line in out.split("\n"):
                if line.startswith("PID:"):
                    lines.append(f"  进程PID: {line[4:]}")
                elif line.startswith("UPTIME:"):
                    lines.append(f"  运行时长: {line[7:]}")
                elif line.startswith("LISTEN_PORTS:"):
                    lines.append(f"  监听端口数: {line[13:]}")
        elif "OA_STOPPED" in out:
            lines.append("  [严重] OA应用服务未运行！")
            lines.append("  建议: 检查 Tomcat/Java 进程")
            lines.append("  建议: 查看应用日志 catalina.out")

        return "\n".join(lines)

    def check_disk(self) -> str:
        """真实磁盘检测"""
        threshold = config.get("inspection.disk_threshold", 85)
        cmd = INSPECTION_COMMANDS["disk"]
        out, err = self._exec_command(cmd["command"], cmd["timeout"])

        if err and not out:
            return f"[{self.name}] 磁盘检测失败: {err}"

        lines = [f"[{self.name}] 磁盘使用检测:"]
        has_warning = False

        for line in out.split("\n"):
            line = line.strip()
            if not line or line.startswith("==="):
                continue

            # 解析 df 输出（各种格式）
            parts = line.split()
            if len(parts) >= 2:
                mount = parts[0]
                # 找到包含 % 的字段
                pct_str = ""
                for p in parts:
                    if "%" in p:
                        pct_str = p.replace("%", "")
                        break

                if pct_str:
                    try:
                        usage = int(pct_str)
                        if usage >= threshold:
                            has_warning = True
                            lines.append(f"  [告警] {mount}: {usage}% (阈值 {threshold}%)")
                        else:
                            lines.append(f"  [正常] {mount}: {usage}%")
                    except ValueError:
                        lines.append(f"  {mount}: {pct_str}%")

        if not has_warning:
            lines.append("  总结: 所有磁盘正常")
        else:
            lines.append(f"  总结: 存在磁盘告警！请及时清理或扩容")

        return "\n".join(lines)

    def check_memory(self) -> str:
        """真实内存检测"""
        threshold = config.get("inspection.memory_threshold", 90)
        cmd = INSPECTION_COMMANDS["memory"]
        out, err = self._exec_command(cmd["command"], cmd["timeout"])

        if err and not out:
            return f"[{self.name}] 内存检测失败: {err}"

        lines = [f"[{self.name}] 内存使用检测:"]

        for line in out.split("\n"):
            if line.startswith("MEM_TOTAL:"):
                parts = line.split()
                data = {}
                for p in parts:
                    if ":" in p:
                        k, v = p.split(":", 1)
                        data[k] = v

                total = data.get("MEM_TOTAL", "?")
                used = data.get("MEM_USED", "?")
                free = data.get("MEM_FREE", "?")
                pct = data.get("MEM_PCT", "0")

                try:
                    pct_val = float(pct)
                    if pct_val >= threshold:
                        lines.append(f"  [告警] 内存使用率过高: {pct}% (阈值 {threshold}%)")
                    else:
                        lines.append(f"  [正常] 内存使用率: {pct}%")
                except ValueError:
                    lines.append(f"  内存使用率: {pct}%")

                lines.append(f"  总内存: {total}MB | 已用: {used}MB | 可用: {free}MB")

                if pct_val >= threshold:
                    lines.append(f"  建议: 排查内存泄漏进程 top -o %MEM")

        return "\n".join(lines)

    def run_full_inspection(self) -> List[Dict]:
        """
        执行完整巡检（全部5项），返回结构化结果列表。

        Returns:
            [{"check_type": "ports", "check_type_cn": "端口检测",
              "result": "...", "is_simulated": False}, ...]
        """
        results = []
        check_methods = [
            ("ports", "端口检测", self.check_ports),
            ("nginx", "Nginx服务", self.check_nginx),
            ("oa", "OA应用服务", self.check_oa_service),
            ("disk", "磁盘使用", self.check_disk),
            ("memory", "内存使用", self.check_memory),
        ]

        for check_type, name_cn, method in check_methods:
            try:
                result_text = method()
            except Exception as e:
                result_text = f"[错误] {name_cn} 检测异常: {str(e)}"

            results.append({
                "check_type": check_type,
                "check_type_cn": name_cn,
                "target": self.name,
                "result": result_text,
                "is_simulated": False,
            })

        self.disconnect()
        return results


def run_ssh_inspection() -> Dict:
    """
    对配置中所有 SSH 主机执行巡检。
    根据 config.yaml 中 inspection.mode 决定行为。

    支持的模式: simulated | ssh | local | auto
    - simulated: 随机模拟数据
    - ssh: 远程SSH真实检测
    - local: 本机Windows命令真实检测
    - auto: 优先SSH → local → 模拟（逐级降级）
    """
    mode = config.get("inspection.mode", "simulated")

    # ---- 纯模拟模式 ----
    if mode == "simulated":
        return {"success": True, "mode": "simulated", "results": [], "report": ""}

    # ---- 本机检测模式 ----
    if mode == "local":
        return _try_local_inspection()

    # ---- SSH 远程模式 / auto 自动模式 ----
    if mode in ("ssh", "auto"):
        ssh_hosts = config.get("inspection.ssh_hosts", [])
        if not ssh_hosts:
            if mode == "ssh":
                return {"success": False, "mode": "ssh",
                         "error": "未配置 SSH 主机（config.yaml → inspection.ssh_hosts）"}
            # auto 模式无 SSH 配置 → 尝试 local
            return _try_local_inspection()

        all_results: list[dict] = []
        hosts_success = 0
        errors: list[str] = []

        for host_cfg in ssh_hosts:
            try:
                inspector = RealInspector(host_cfg)
                host_results = inspector.run_full_inspection()
                all_results.extend(host_results)
                hosts_success += 1
            except Exception as e:
                errors.append(f"[{host_cfg.get('name', host_cfg.get('host', 'unknown'))}] {e}")

        if hosts_success > 0:
            report_lines = [
                "=" * 55,
                f"  OA系统巡检报告（SSH 真实检测）",
                f"  巡检时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"  巡检模式: SSH 远程服务器",
                f"  成功: {hosts_success}/{len(ssh_hosts)} 台主机",
                "=" * 55,
                "",
            ]
            for r in all_results:
                report_lines.append(r["result"])
                report_lines.append("")
            report_lines.append("=" * 55)
            report_lines.append(f"  共 {len(all_results)} 项检测完成")
            report_lines.append("=" * 55)

            return {
                "success": True,
                "mode": "ssh",
                "results": all_results,
                "report": "\n".join(report_lines),
            }
        else:
            error_msg = "; ".join(errors) if errors else "SSH 连接失败"
            if mode == "ssh":
                return {"success": False, "mode": "ssh", "error": error_msg}
            # auto 模式 SSH 全部失败 → 尝试 local
            logger.warning(f"SSH 全部失败 ({error_msg})，尝试本机检测...")
            local_result = _try_local_inspection()
            if local_result["success"]:
                return local_result
            return {"success": False, "mode": "ssh", "error": error_msg}

    # ---- 未识别的模式 ----
    return {"success": True, "mode": "simulated", "results": [], "report": ""}


def _try_local_inspection() -> Dict:
    """尝试本机 Windows 检测，失败则返回降级标记"""
    try:
        inspector = LocalInspector()
        results = inspector.run_full_inspection()
        report_lines = [
            "=" * 55,
            f"  OA系统巡检报告（本机真实检测）",
            f"  巡检时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"  巡检模式:  本机 Windows 命令检测",
            f"  主机名: {inspector.hostname}",
            "=" * 55,
            "",
        ]
        for r in results:
            report_lines.append(r["result"])
            report_lines.append("")
        report_lines.append("=" * 55)
        report_lines.append(f"  共 {len(results)} 项检测完成")
        report_lines.append("=" * 55)

        return {
            "success": True,
            "mode": "local",
            "results": results,
            "report": "\n".join(report_lines),
        }
    except Exception as e:
        logger.warning(f"本机检测失败: {e}")
        return {"success": False, "mode": "local", "error": str(e)}

class LocalInspector:
    """
    本机巡检器 — 使用 Windows 原生命令检测当前机器。
    无需 SSH，无需额外权限，直接调用 subprocess 执行系统命令。
    """

    def __init__(self):
        import socket
        self.hostname = socket.gethostname()

    def _run_cmd(self, command: str, timeout: int = 10) -> str:
        """执行 Windows 命令并返回输出"""
        import subprocess
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=timeout, encoding="gbk", errors="ignore"
            )
            return result.stdout.strip() or result.stderr.strip()
        except subprocess.TimeoutExpired:
            return ""
        except Exception as e:
            return f"命令执行失败: {e}"

    def check_ports(self) -> str:
        """本机端口检测 (netstat)"""
        out = self._run_cmd("netstat -ano | findstr LISTENING")
        lines = [f"[{self.hostname}] 端口检测:"]

        ports_map = {80: "HTTP", 443: "HTTPS", 8080: "OA应用", 3306: "MySQL", 6379: "Redis"}
        for port, name in ports_map.items():
            if f":{port} " in out or f":{port}\r" in out or f":{port}\n" in out:
                lines.append(f"  [正常] 端口 {port} ({name}): 监听中")
            else:
                lines.append(f"  [异常] 端口 {port} ({name}): 未监听")

        return "\n".join(lines)

    def check_nginx(self) -> str:
        """本机 Nginx 检测 (sc query + tasklist)"""
        sc_out = self._run_cmd('sc query nginx')
        task_out = self._run_cmd('tasklist /FI "IMAGENAME eq nginx.exe" 2>nul')

        lines = [f"[{self.hostname}] Nginx服务检测:"]
        if "RUNNING" in sc_out or "nginx.exe" in task_out:
            lines.append("  [正常] Nginx 进程运行中")
            # 提取 PID
            for line in task_out.split("\n"):
                if "nginx.exe" in line:
                    parts = line.split()
                    if len(parts) >= 2:
                        lines.append(f"  PID: {parts[-1]}")
                    break
        else:
            lines.append("  [信息] Nginx 未安装或未运行")

        return "\n".join(lines)

    def check_oa_service(self) -> str:
        """本机 OA 服务检测 (tasklist java)"""
        out = self._run_cmd('tasklist /FI "IMAGENAME eq java.exe" 2>nul')
        lines = [f"[{self.hostname}] OA应用服务检测:"]

        if "java.exe" in out:
            lines.append("  [正常] Java 进程运行中")
            java_count = out.count("java.exe")
            lines.append(f"  Java 进程数: {java_count}")
            # 内存使用
            for line in out.split("\n"):
                if "java.exe" in line:
                    parts = line.split()
                    if len(parts) >= 5:
                        lines.append(f"  内存: {parts[-2]} KB")
                    break
        else:
            lines.append("  [信息] 未检测到 Java 进程")
            lines.append("  提示: OA 服务通常运行在 Tomcat/Java 环境")

        return "\n".join(lines)

    def check_disk(self) -> str:
        """本机磁盘检测 (wmic)"""
        threshold = config.get("inspection.disk_threshold", 85)
        out = self._run_cmd(
            'wmic logicaldisk where "DriveType=3" get Caption,Size,FreeSpace /format:csv 2>nul'
        )
        lines = [f"[{self.hostname}] 磁盘使用检测:"]

        for line in out.split("\n"):
            line = line.strip()
            if not line or "Node" in line or "Caption" in line:
                continue
            # WMIC CSV 列顺序: Node, Caption, FreeSpace, Size
            parts = line.split(",")
            if len(parts) >= 4:
                drive = parts[1].strip()
                try:
                    free = int(parts[2])   # FreeSpace 在第2列
                    size = int(parts[3])   # Size 在第3列
                    if size > 0:
                        pct = round((size - free) / size * 100, 1)
                        size_gb = round(size / 1073741824, 1)
                        if pct >= threshold:
                            lines.append(f"  [告警] {drive}: {pct}% ({size_gb}GB, 阈值{threshold}%)")
                        else:
                            lines.append(f"  [正常] {drive}: {pct}% ({size_gb}GB)")
                except (ValueError, ZeroDivisionError):
                    lines.append(f"  {drive}: 解析失败")

        return "\n".join(lines) if len(lines) > 1 else f"[{self.hostname}] 磁盘检测: 无数据"

    def check_memory(self) -> str:
        """本机内存检测 (wmic)"""
        threshold = config.get("inspection.memory_threshold", 90)
        out = self._run_cmd(
            'wmic OS get TotalVisibleMemorySize,FreePhysicalMemory /format:csv 2>nul'
        )
        lines = [f"[{self.hostname}] 内存使用检测:"]

        for line in out.split("\n"):
            line = line.strip()
            if not line or "Node" in line or "Total" in line:
                continue
            # WMIC CSV 列顺序: Node, FreePhysicalMemory, TotalVisibleMemorySize
            parts = line.split(",")
            if len(parts) >= 3:
                try:
                    free_kb = int(parts[1])   # FreePhysicalMemory 在第1列
                    total_kb = int(parts[2])  # TotalVisibleMemorySize 在第2列
                    used_kb = total_kb - free_kb
                    pct = round(used_kb / total_kb * 100, 1)
                    total_gb = round(total_kb / 1048576, 1)
                    used_gb = round(used_kb / 1048576, 1)

                    if pct >= threshold:
                        lines.append(f"  [告警] 内存使用率: {pct}% (阈值{threshold}%)")
                    else:
                        lines.append(f"  [正常] 内存使用率: {pct}%")
                    lines.append(f"  总内存: {total_gb}GB | 已用: {used_gb}GB")
                except (ValueError, ZeroDivisionError):
                    lines.append("  内存数据解析失败")

        return "\n".join(lines) if len(lines) > 1 else f"[{self.hostname}] 内存检测: 无数据"

    def run_full_inspection(self) -> List[Dict]:
        """执行完整本机巡检"""
        results = []
        check_methods = [
            ("ports", "端口检测", self.check_ports),
            ("nginx", "Nginx服务", self.check_nginx),
            ("oa", "OA应用服务", self.check_oa_service),
            ("disk", "磁盘使用", self.check_disk),
            ("memory", "内存使用", self.check_memory),
        ]

        for check_type, name_cn, method in check_methods:
            try:
                result_text = method()
            except Exception as e:
                result_text = f"[错误] {name_cn} 检测异常: {str(e)}"

            results.append({
                "check_type": check_type,
                "check_type_cn": name_cn,
                "target": self.hostname,
                "result": result_text,
                "is_simulated": False,
            })

        return results
