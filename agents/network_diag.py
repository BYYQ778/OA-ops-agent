"""
网络诊断工具模块
---------------
提供运维日常网络排查的常用工具：
- Ping 连通性检测
- TCP 端口连通性检查
- DNS 解析（A/AAAA/MX/CNAME）
- 路由追踪（traceroute/tracert）
- HTTP 健康检查

纯 Python 实现（subprocess + socket），无需 LLM。
支持 Windows 和 Linux 双平台命令适配。

使用方式:
    from agents.network_diag import (
        ping_host, check_tcp_port, dns_resolve,
        traceroute_host, http_health_check
    )
    print(ping_host.invoke({"host": "oa.example.com"}))
"""

import sys
import socket
import subprocess
import ipaddress
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime

from langchain.tools import tool

from utils.logger import get_logger
from utils.prompt_safety import UNTRUSTED_DATA_GUARD
from utils.config import config

logger = get_logger(__name__)

IS_WINDOWS = sys.platform == "win32"
DEFAULT_TIMEOUT = config.get("network_diag.ping_timeout", 5)


def _is_safe_host(host: str) -> bool:
    """校验主机名/域名/IP 格式，拒绝 shell 元字符与参数注入。"""
    if not host or len(host) > 253 or host.startswith("-"):
        return False
    # 禁止 shell 元字符与空白（防御注入；shell=False 双保险）
    if any(c in host for c in "&|;`$<>(){}[]'\"\\ \t\n"):
        return False
    # IPv4 / IPv6
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    # 按 DNS 标签校验，保留内网下划线主机名和完整域名末尾的点。
    name = host[:-1] if host.endswith(".") else host
    return all(
        re.fullmatch(r"[A-Za-z0-9_](?:[A-Za-z0-9_\-]{0,61}[A-Za-z0-9_])?", label)
        for label in name.split(".")
    )


def _run_command(args, timeout: int = 15) -> str:
    """执行系统命令并返回输出（跨平台，参数数组 + shell=False 防注入）"""
    try:
        result = subprocess.run(
            args,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="gbk" if IS_WINDOWS else "utf-8",
            errors="ignore",
        )
        return result.stdout.strip() or result.stderr.strip()
    except subprocess.TimeoutExpired:
        return "命令执行超时"
    except Exception as e:
        return f"命令执行失败: {e}"


# ========== 工具函数 ==========

@tool
def ping_host(host: str) -> str:
    """
    对目标主机执行 ICMP Ping 检测，返回连通性和延迟信息。

    Args:
        host: 目标 IP 或域名，如 192.168.1.1 或 oa.example.com

    Returns:
        Ping 检测结果，含丢包率和平均延迟
    """
    if not host or not host.strip():
        return "[错误] 请输入目标 IP 或域名"

    host = host.strip()

    if not _is_safe_host(host):
        return "[错误] 目标主机格式非法（仅支持 IP 或域名）"

    # 构建平台适配的 ping 命令（参数数组，防命令注入）
    if IS_WINDOWS:
        args = ["ping", "-n", "4", "-w", "3000", host]
    else:
        args = ["ping", "-c", "4", "-W", "3", host]

    output = _run_command(args, timeout=15)

    if not output or "命令执行" in output:
        return f"[错误] Ping 执行失败: {output}"

    # 解析结果
    lines = ["=" * 50, "  ICMP Ping 诊断结果", "=" * 50,
             f"目标: {host}", f"检测时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", ""]

    # 提取丢包率（Windows 和 Linux 格式不同）
    min_ping = avg_ping = max_ping = None

    if IS_WINDOWS:
        loss_match = re.search(r'(\d+)%.*丢失', output)
        avg_match = re.search(r'平均\s*=\s*(\d+)ms', output)
        min_match = re.search(r'最短\s*=\s*(\d+)ms', output)
        max_match = re.search(r'最长\s*=\s*(\d+)ms', output)
        min_ping = min_match.group(1) if min_match else None
        avg_ping = avg_match.group(1) if avg_match else None
        max_ping = max_match.group(1) if max_match else None
    else:
        loss_match = re.search(r'(\d+)%.*loss', output, re.IGNORECASE)
        # mdev format: min/avg/max/mdev
        rtt_match = re.search(r'rtt.*?=\s*([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)\s*ms', output)
        if rtt_match:
            min_ping = rtt_match.group(1)
            avg_ping = rtt_match.group(2)
            max_ping = rtt_match.group(3)
        else:
            avg_match2 = re.search(r'avg.*?([\d.]+)\s*ms', output)
            avg_ping = avg_match2.group(1) if avg_match2 else None

    # 判断连通性
    if loss_match:
        loss_pct = int(loss_match.group(1))
        if loss_pct == 0:
            status = "🟢 网络正常"
        elif loss_pct < 50:
            status = f"🟡 部分丢包 ({loss_pct}%)"
        elif loss_pct < 100:
            status = f"🔴 严重丢包 ({loss_pct}%)"
        else:
            status = "🔴 目标不可达（100% 丢包）"
        lines.append(f"连通性: {status}")
        lines.append(f"丢包率: {loss_pct}%")
    else:
        lines.append(f"连通性: ❌ ping 请求无响应（目标不可达或禁 ping）")

    # 延迟
    if avg_ping:
        parts = []
        if min_ping:
            parts.append(f"最少={min_ping}ms")
        parts.append(f"平均={avg_ping}ms")
        if max_ping:
            parts.append(f"最长={max_ping}ms")
        lines.append(f"延迟: {', '.join(parts)}")

    if loss_match and int(loss_match.group(1)) == 100:
        lines.append("")
        lines.append("💡 排查建议:")
        lines.append("  1. 检查目标主机是否开机、网络是否正常")
        lines.append("  2. 检查防火墙是否拦截了 ICMP 包")
        lines.append("  3. 检查本机网络: ipconfig /all (Windows) 或 ip addr (Linux)")
        lines.append("  4. 尝试 ping 网关或公网地址（如 8.8.8.8）定位问题范围")

    lines.append("=" * 50)
    return "\n".join(lines)


@tool
def check_tcp_port(host_port: str) -> str:
    """
    检测目标主机的 TCP 端口是否开放（模拟 telnet 效果）。

    Args:
        host_port: 主机和端口，格式为 host:port，如 192.168.1.100:3306

    Returns:
        端口连通性检测结果
    """
    if not host_port or ":" not in host_port:
        return "[错误] 请按 host:port 格式输入，如 192.168.1.100:3306"

    host, port_str = host_port.rsplit(":", 1)
    host = host.strip()
    try:
        port = int(port_str.strip())
    except ValueError:
        return f"[错误] 端口号无效: {port_str}"

    if not _is_safe_host(host):
        return "[错误] 目标主机格式非法（仅支持 IP 或域名）"
    if not 1 <= port <= 65535:
        return "[错误] 端口号必须在 1 到 65535 之间"

    start_time = time.time()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)

    try:
        result_code = sock.connect_ex((host, port))
        elapsed_ms = round((time.time() - start_time) * 1000, 1)

        if result_code == 0:
            # 尝试获取 banner
            banner = ""
            try:
                sock.settimeout(2)
                # 发送一个空包探测
                sock.send(b"\r\n")
                banner = sock.recv(1024).decode("utf-8", errors="ignore").strip()[:80]
            except (socket.timeout, OSError):
                pass

            lines = [
                "=" * 50,
                "  TCP 端口连通性检测",
                "=" * 50,
                f"目标: {host}:{port}",
                f"状态: 🟢 端口开放",
                f"连接耗时: {elapsed_ms}ms",
            ]
            if banner:
                lines.append(f"服务Banner: {banner}")
            lines.append("=" * 50)
            return "\n".join(lines)
        else:
            return (
                f"{'='*50}\n"
                f"  TCP 端口连通性检测\n"
                f"{'='*50}\n"
                f"目标: {host}:{port}\n"
                f"状态: 🔴 端口不可达 (错误码: {result_code})\n"
                f"耗时: {elapsed_ms}ms\n"
                f"💡 排查: 确认目标服务是否启动、防火墙是否放行该端口\n"
                f"{'='*50}"
            )
    except socket.timeout:
        return f"[不可达] {host}:{port} — 连接超时（5秒），端口可能被防火墙屏蔽"
    except socket.gaierror:
        return f"[错误] DNS 解析失败: {host}"
    except Exception as e:
        return f"[错误] 端口检测异常: {e}"
    finally:
        sock.close()


@tool
def dns_resolve(domain: str) -> str:
    """
    对域名进行 DNS 解析，返回 A/AAAA/CNAME/MX 记录。

    Args:
        domain: 目标域名，如 example.com

    Returns:
        DNS 解析结果，含各类记录
    """
    if not domain or not domain.strip():
        return "[错误] 请输入域名"

    domain = domain.strip()
    if not _is_safe_host(domain):
        return "[错误] 域名格式非法"

    lines = [
        "=" * 50,
        f"  DNS 解析结果: {domain}",
        f"  检测时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 50,
    ]

    # --- A 记录 ---
    try:
        addrs = socket.getaddrinfo(domain, None, socket.AF_INET)
        a_records = sorted(set(addr[4][0] for addr in addrs))
        lines.append(f"📌 A 记录 (IPv4): {len(a_records)} 个")
        for a in a_records:
            lines.append(f"    → {a}")
    except socket.gaierror:
        lines.append("📌 A 记录: 解析失败（域名不存在？）")

    # --- AAAA 记录 ---
    try:
        addrs = socket.getaddrinfo(domain, None, socket.AF_INET6)
        aaaa_records = sorted(set(addr[4][0] for addr in addrs))
        if aaaa_records:
            lines.append(f"📌 AAAA 记录 (IPv6): {len(aaaa_records)} 个")
            for aaaa in aaaa_records[:3]:
                lines.append(f"    → {aaaa}")
            if len(aaaa_records) > 3:
                lines.append(f"    ... 共 {len(aaaa_records)} 个")
    except socket.gaierror:
        pass  # 无 IPv6 不报错

    # --- CNAME 记录 ---
    output = _run_command(["nslookup", "-type=CNAME", domain], timeout=10)

    if output and "canonical" in output.lower():
        cname_match = re.search(r'canonical name\s*=\s*(\S+)', output, re.IGNORECASE)
        if cname_match:
            lines.append(f"📌 CNAME 记录: {cname_match.group(1)}")
    elif output and output.strip() and "server can't find" not in output.lower():
        cname_val = output.strip().rstrip(".")
        if cname_val:
            lines.append(f"📌 CNAME 记录: {cname_val}")

    # --- MX 记录 ---
    output = _run_command(["nslookup", "-type=MX", domain], timeout=10)

    if output and "mail exchanger" in output.lower():
        mx_records = re.findall(r'MX preference\s*=\s*(\d+).*?mail exchanger\s*=\s*(\S+)', output, re.IGNORECASE)
        if mx_records:
            lines.append(f"📌 MX 记录 (邮件): {len(mx_records)} 条")
            for preference, mx_host in sorted(mx_records, key=lambda x: int(x[0])):
                lines.append(f"    → {mx_host} (优先级: {preference})")
    elif output and output.strip() and "server can't find" not in output.lower():
        lines.append(f"📌 MX 记录:")
        for line in output.strip().split("\n"):
            line = line.strip()
            if line:
                lines.append(f"    → {line}")

    lines.append("=" * 50)
    return "\n".join(lines)


@tool
def traceroute_host(host: str) -> str:
    """
    对目标主机执行路由追踪，显示数据包经过的路径。

    Args:
        host: 目标 IP 或域名

    Returns:
        路由追踪结果
    """
    if not host or not host.strip():
        return "[错误] 请输入目标 IP 或域名"

    host = host.strip()

    lines = [
        "=" * 50,
        f"  路由追踪 (Traceroute)",
        "=" * 50,
        f"目标: {host}",
        f"检测时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "注意: 追踪可能需要较长时间（最多30秒），请耐心等待...",
        "",
    ]

    if not _is_safe_host(host):
        return "[错误] 目标主机格式非法（仅支持 IP 或域名）"

    if IS_WINDOWS:
        args = ["tracert", "-d", "-h", "15", "-w", "2000", host]
    else:
        args = ["traceroute", "-n", "-m", "15", "-w", "2", host]

    output = _run_command(args, timeout=35)
    if output:
        lines.append(output)
    else:
        lines.append("[提示] 无输出，可能需要管理员/root 权限")

    lines.append("")
    lines.append("💡 说明:")
    lines.append("  * 星号表示该跳未响应（防火墙丢弃或超时）")
    lines.append("  每一跳的 IP 代表数据包经过的一个路由节点")
    lines.append("=" * 50)
    return "\n".join(lines)


#: 云元数据服务地址（SSRF 防护：内网探测是本工具定位，元数据端点必须硬阻断）
_METADATA_IPS = frozenset({"169.254.169.254", "100.100.100.200", "fd00:ec2::254"})


class _RedirectBlockedError(Exception):
    """重定向目标命中 SSRF 防护规则。"""

    def __init__(self, target: str, reason: str) -> None:
        super().__init__(f"{reason}: {target}")
        self.target = target
        self.reason = reason


def _resolve_host_ips(host: str) -> list[str]:
    """解析主机名的全部 A/AAAA 地址；解析失败返回空列表（由请求侧给出自然错误）。"""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return []
    ips: list[str] = []
    for info in infos:
        addr = str(info[4][0])
        if addr not in ips:
            ips.append(addr)
    return ips


def _url_host(url: str) -> str:
    """提取并校验 URL 主机名；不合法返回空串（仅接受 http/https + IP/域名）。"""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return ""
    hostname = parsed.hostname.strip()
    if not hostname or len(hostname) > 253:
        return ""
    try:
        ipaddress.ip_address(hostname)
        return hostname
    except ValueError:
        pass
    name = hostname[:-1] if hostname.endswith(".") else hostname
    labels_ok = all(
        re.fullmatch(r"[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?", label)
        for label in name.split(".")
    )
    return hostname if labels_ok else ""


def _forbidden_target_reason(host: str) -> str:
    """SSRF 防护判定：阻断云元数据/链路本地/组播地址；内网地址按工具定位放行。

    Returns:
        阻断原因文案（空串 = 允许探测）。
    """
    try:
        candidates = [str(ipaddress.ip_address(host))]
    except ValueError:
        candidates = _resolve_host_ips(host)
    for addr in candidates:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if getattr(ip, "ipv4_mapped", None) is not None:
            ip = ip.ipv4_mapped
        if str(ip) in _METADATA_IPS:
            return "云元数据地址"
        if ip.is_link_local:
            return "链路本地地址"
        if ip.is_multicast or ip.is_unspecified:
            return "组播或未指定地址"
    return ""


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """跟随重定向时逐跳校验目标（受阻目标直接拒绝，最多 3 跳）。"""

    max_redirections = 3

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = _url_host(newurl)
        if not host:
            raise _RedirectBlockedError(newurl, "重定向目标非法")
        reason = _forbidden_target_reason(host)
        if reason:
            raise _RedirectBlockedError(newurl, reason)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_url(req, timeout, context):
    """执行 HTTP 请求（模块级便于测试打桩）；重定向经逐跳安全校验。"""
    handlers: list = [_SafeRedirectHandler()]
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    opener = urllib.request.build_opener(*handlers)
    return opener.open(req, timeout=timeout)


def _tls_verify_hint() -> list[str]:
    """证书校验失败的修复指引（错误分支共用）。"""
    return [
        "💡 说明: 默认校验服务端证书；自签证书内网站点可在 config.yaml 设置",
        "   network_diag.tls_verify: false（或改用 SSL 证书检查工具）",
    ]


@tool
def http_health_check(url: str) -> str:
    """
    对 HTTP/HTTPS URL 进行健康检查，返回状态码、响应时间、响应头摘要。

    Args:
        url: 完整的 HTTP/HTTPS URL，如 https://oa.example.com

    Returns:
        HTTP 健康检查结果
    """
    if not url or not url.strip():
        return "[错误] 请输入完整的 URL（含 http:// 或 https://）"

    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    host = _url_host(url)
    if not host:
        return f"[错误] URL 不合法（仅支持 http/https + IP/域名）: {url}"

    reason = _forbidden_target_reason(host)
    if reason:
        return (
            f"[错误] 目标地址不被允许（{reason}）: {host}\n"
            "说明: 云元数据/链路本地地址已硬阻断（SSRF 防护）；内网地址探测请使用 ping/端口检查工具。"
        )

    verify_tls = bool(config.get("network_diag.tls_verify", True))

    lines = [
        "=" * 50,
        "  HTTP 服务健康检查",
        "=" * 50,
        f"URL: {url}",
        f"检测时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    if url.startswith("https://") and not verify_tls:
        lines.append("⚠️ 已按配置跳过 TLS 证书校验（network_diag.tls_verify=false）")
    lines.append("")

    import ssl
    if url.startswith("https://"):
        ctx = ssl.create_default_context()
        if not verify_tls:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
    else:
        ctx = None

    try:
        start_time = time.time()
        req = urllib.request.Request(url, method="GET", headers={
            "User-Agent": "OA-Ops-Agent/2.0 Network Health Check"
        })

        with _open_url(req, 10, ctx) as resp:
            elapsed = round((time.time() - start_time) * 1000)
            status_code = resp.status
            content_length = resp.headers.get("Content-Length", "未知")
            content_type = resp.headers.get("Content-Type", "未知")
            server = resp.headers.get("Server", "未知")

        # 状态评估
        if 200 <= status_code < 300:
            status_icon = "🟢 正常"
        elif status_code in (301, 302, 307, 308):
            status_icon = f"🟡 重定向到: {resp.headers.get('Location', '未知')}"
            lines.append(f"重定向: {resp.headers.get('Location', '未知')}")
        elif 400 <= status_code < 500:
            status_icon = "🔴 客户端错误"
        else:
            status_icon = "🔴 服务端错误"

        lines.extend([
            f"状态: {status_icon}",
            f"HTTP 状态码: {status_code}",
            f"响应时间: {elapsed}ms",
            f"服务器: {server}",
            f"Content-Type: {content_type}",
            f"Content-Length: {content_length}",
        ])

        if elapsed > 2000:
            lines.append("")
            lines.append("⚠️ 响应时间超过 2 秒，可能存在性能问题")

        if status_code >= 500:
            lines.append("")
            lines.append("💡 5xx 错误排查建议:")
            lines.append("  1. 检查后端服务是否正常运行")
            lines.append("  2. 查看服务日志排查根因")
            lines.append("  3. 检查反向代理（Nginx/HAProxy）状态")

    except _RedirectBlockedError as e:
        lines.append(f"状态: 🔴 重定向目标已阻断（{e.reason}）: {e.target}")
    except urllib.error.HTTPError as e:
        lines.append(f"状态: 🔴 HTTP {e.code} — {e.reason}")
        if e.code in (301, 302, 303, 307, 308):
            location = e.headers.get("Location", "未知") if e.headers else "未知"
            lines.append(f"重定向未被跟随（受阻或超过 3 跳）: {location}")
    except urllib.error.URLError as e:
        if isinstance(e.reason, ssl.SSLCertVerificationError):
            lines.append(f"状态: 🔴 TLS 证书校验失败 — {str(e.reason)[:100]}")
            lines.append("")
            lines.extend(_tls_verify_hint())
        else:
            lines.append(f"状态: 🔴 连接失败 — {e.reason}")
            lines.append("")
            lines.append("💡 排查建议:")
            lines.append("  1. 确认 URL 是否正确")
            lines.append("  2. 检查 DNS 解析是否正常")
            lines.append("  3. 确认服务器端口是否开放")
    except ssl.SSLCertVerificationError as e:
        lines.append(f"状态: 🔴 TLS 证书校验失败 — {str(e)[:100]}")
        lines.append("")
        lines.extend(_tls_verify_hint())
    except ssl.SSLError as e:
        lines.append(f"状态: 🔴 SSL 握手失败 — {str(e)[:100]}")
    except Exception as e:
        lines.append(f"[错误] 健康检查异常: {str(e)[:200]}")

    lines.append("=" * 50)
    return "\n".join(lines)


# ========== 工具汇总 ==========

NETWORK_DIAG_TOOLS = [
    ping_host,
    check_tcp_port,
    dns_resolve,
    traceroute_host,
    http_health_check,
]

# ========== LLM Agent ==========

NETWORK_DIAG_SYSTEM_PROMPT = """你是一名网络诊断专家，帮助运维工程师快速定位网络问题。

你可以使用以下工具进行网络诊断：
- ping_host: 检测主机连通性和延迟
- check_tcp_port: 检测 TCP 端口是否开放
- dns_resolve: DNS 解析
- traceroute_host: 路由追踪
- http_health_check: HTTP 服务健康检查

诊断流程：
1. 先 ping 目标主机，确认网络层是否可达
2. 如果 ping 通但服务不通，检测具体端口
3. 如果是域名访问问题，先做 DNS 解析
4. 如果是 HTTP 服务，做健康检查获取状态码
5. 排查跨网段问题，使用路由追踪

请根据用户描述的问题，选择合适的工具进行诊断，并给出明确的排查结论。""" + UNTRUSTED_DATA_GUARD


class NetworkDiagAgent:
    """
    网络诊断 Agent（LLM 增强版）。

    使用方式：
        agent = NetworkDiagAgent(llm_api_key="...")
        result = agent.diagnose("oa.example.com 访问很慢")
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
                max_tokens=1536,
            )
            self._agent = create_agent(
                model=llm,
                tools=NETWORK_DIAG_TOOLS,
                system_prompt=NETWORK_DIAG_SYSTEM_PROMPT,
            )
            logger.info("网络诊断Agent初始化完成")
        except Exception as e:
            logger.warning(f"网络诊断Agent初始化失败: {e}")

    def diagnose(self, problem_description: str) -> str:
        """根据问题描述执行网络诊断"""
        if self._agent:
            try:
                result = self._agent.invoke({
                    "messages": [{"role": "user", "content": f"请帮我诊断以下网络问题:\n{problem_description}"}]
                })
                msgs = result.get("messages", [])
                if msgs:
                    return msgs[-1].content
            except Exception as e:
                logger.warning(f"Agent诊断异常，降级: {e}")

        # 降级：根据关键词建议用户使用具体工具
        return (
            f"[离线模式] LLM 不可用，请直接使用以下工具进行诊断:\n"
            f"  • ping 检测: 输入主机名或 IP\n"
            f"  • 端口检测: 输入 host:port，如 192.168.1.1:3306\n"
            f"  • DNS 解析: 输入域名\n"
            f"  • 路由追踪: 输入目标 IP/域名\n"
            f"  • HTTP 检查: 输入完整 URL\n"
        )


# ========== 快速测试入口 ==========

if __name__ == "__main__":
    print("=== 网络诊断工具测试 ===\n")

    # 测试1: Ping
    print("--- Ping 测试 (8.8.8.8) ---")
    print(ping_host.invoke({"host": "8.8.8.8"}))

    print("\n--- DNS 解析 (www.baidu.com) ---")
    print(dns_resolve.invoke({"domain": "www.baidu.com"}))

    print("\n--- TCP 端口检测 (www.baidu.com:443) ---")
    print(check_tcp_port.invoke({"host_port": "www.baidu.com:443"}))

    print("\n--- HTTP 健康检查 ---")
    print(http_health_check.invoke({"url": "https://www.baidu.com"}))
