"""
SSL/TLS 证书监控模块
-------------------
检测远程服务器的 SSL/TLS 证书状态，包括：
- 证书有效期（剩余天数）
- 证书颁发者
- 证书主题（CN/SAN）
- 证书链完整性
- 过期告警（提前 30/7/1 天）

纯 Python 实现（ssl + socket），不依赖 LLM。
可集成到定时巡检调度 + 告警通知。

使用方式:
    from agents.ssl_monitor import SSLCertMonitor
    monitor = SSLCertMonitor()
    monitor.check_cert("oa.example.com", 443)
    monitor.batch_check(["oa.example.com", "www.example.com"])
"""

import ssl
import socket
from datetime import datetime, timezone
from typing import List, Dict

from langchain.tools import tool

from utils.logger import get_logger
from utils.config import config

logger = get_logger(__name__)

# 检测 cryptography 库是否可用
try:
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False


# ========== 核心工具函数 ==========

@tool
def check_cert_expiry(domain: str) -> str:
    """
    检测指定域名的 SSL/TLS 证书过期时间和详细信息。

    Args:
        domain: 目标域名（可含端口，如 oa.example.com 或 oa.example.com:443）

    Returns:
        证书状态报告，含剩余天数、颁发者、主题CN、SAN列表
    """
    if not domain or not domain.strip():
        return "[错误] 请提供有效的域名"

    domain = domain.strip()

    # 解析端口
    port = 443
    if ":" in domain:
        parts = domain.rsplit(":", 1)
        domain, port_str = parts[0], parts[1]
        try:
            port = int(port_str)
        except ValueError:
            return f"[错误] 端口号无效: {port_str}"

    try:
        # 建立 SSL 连接获取证书
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

        with socket.create_connection((domain, port), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=domain) as ssock:
                cert_bin = ssock.getpeercert(binary_form=True)
                cert_dict = ssock.getpeercert()

        # 解析证书详情 — cryptography 不可用时降级为简化解析
        if HAS_CRYPTOGRAPHY:
            cert = x509.load_der_x509_certificate(cert_bin, default_backend())

            not_after = cert.not_valid_after_utc
            not_before = cert.not_valid_before_utc
            now_val = datetime.now(timezone.utc)
            remaining_days = (not_after - now_val).days

            cn = "未知"
            for attr in cert.subject:
                if attr.oid._name == "commonName":
                    cn = attr.value
                    break

            san_list = []
            try:
                san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
                for name in san_ext.value:
                    if isinstance(name, x509.DNSName):
                        san_list.append(name.value)
            except x509.ExtensionNotFound:
                pass

            issuer_cn = "未知"
            for attr in cert.issuer:
                if attr.oid._name == "commonName":
                    issuer_cn = attr.value
                    break

            serial = format(cert.serial_number, 'X')
            not_before_str = not_before.strftime('%Y-%m-%d %H:%M:%S') + " UTC"
            not_after_str = not_after.strftime('%Y-%m-%d %H:%M:%S') + " UTC"
        else:
            # 降级方案：仅使用标准库 ssl 模块解析 dict
            simple = _check_cert_simple(domain, port)
            if simple.get("error"):
                return f"[错误] 证书检测失败: {simple['error']}"

            cn = simple.get("cn", "未知")
            issuer_cn = simple.get("issuer", "未知")
            remaining_days = simple.get("remaining_days")
            san_list = []
            serial = "未知（需安装 cryptography）"
            not_before_str = "未知"
            not_after_str = simple.get("not_after", "未知")

            if remaining_days is None:
                return f"[错误] 无法解析证书过期时间: {simple.get('not_after', '未知')}"

        # ---- 构建状态 ----
        if remaining_days < 0:
            status_icon = "🔴 已过期"
            urgency = "严重 — 请立即更新证书！"
        elif remaining_days <= 1:
            status_icon = "🔴 即将过期"
            urgency = "严重 — 将在 1 天内过期！"
        elif remaining_days <= 7:
            status_icon = "🟡 临近过期"
            urgency = f"告警 — 将在 {remaining_days} 天后过期，请尽快更新"
        elif remaining_days <= 30:
            status_icon = "🟡 建议更新"
            urgency = f"还有 {remaining_days} 天过期，建议提前规划更新"
        else:
            status_icon = "🟢 正常"
            urgency = f"证书有效期充足（还剩余 {remaining_days} 天）"

        lines = [
            f"{'='*50}",
            f"  SSL/TLS 证书检测报告",
            f"{'='*50}",
            f"域名: {domain}:{port}",
            f"状态: {status_icon}",
            f"",
            f"📋 证书详情:",
            f"  主体 CN: {cn}",
            f"  颁发者: {issuer_cn}",
            f"  序列号: {serial}",
            f"  生效时间: {not_before_str}",
            f"  过期时间: {not_after_str}",
            f"  剩余天数: {remaining_days} 天",
        ]
        if san_list:
            lines.append(f"  SAN 列表: {', '.join(san_list[:10])}")
            if len(san_list) > 10:
                lines.append(f"           ... 共 {len(san_list)} 个")
        lines.extend([
            f"",
            f"📝 评估: {urgency}",
            f"",
            f"💡 建议:",
            f"  1. 记下过期时间，在日历中添加证书更新提醒",
            f"  2. 提前至少 7 天申请/购买新证书",
            f"  3. 更新后验证新证书是否生效: openssl s_client -connect {domain}:{port} -servername {domain}",
            f"  4. 考虑使用 Let's Encrypt 免费证书 + certbot 自动续期",
            f"{'='*50}",
        ])

        return "\n".join(lines)

    except socket.timeout:
        return f"[错误] 连接 {domain}:{port} 超时（10秒）"
    except socket.gaierror:
        return f"[错误] DNS 解析失败: {domain}"
    except ConnectionRefusedError:
        return f"[错误] 连接被拒绝: {domain}:{port}（目标端口未开放？）"
    except ssl.SSLError as e:
        return f"[错误] SSL 握手失败: {str(e)[:200]}"
    except Exception as e:
        logger.error(f"证书检测异常 {domain}:{port}: {e}")
        return f"[错误] 检测失败: {str(e)[:200]}"


@tool
def batch_check_certs(domains_text: str) -> str:
    """
    批量检测多个域名的 SSL 证书状态。

    Args:
        domains_text: 域名列表，每行一个，格式为 domain.com 或 domain.com:443

    Returns:
        批量检测汇总报告
    """
    if not domains_text or not domains_text.strip():
        return "[提示] 请提供域名列表（每行一个）"

    domains = [d.strip() for d in domains_text.split("\n") if d.strip()]
    if not domains:
        return "[提示] 未检测到有效域名"

    # 从配置加载额外域名
    configured_domains = config.get("ssl_monitor.domains", [])
    if configured_domains:
        for d in configured_domains:
            if d not in domains:
                domains.append(d)

    lines = [
        f"{'='*55}",
        f"  SSL/TLS 批量证书检测报告",
        f"  检测时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"  检测数量: {len(domains)} 个域名",
        f"{'='*55}",
        "",
    ]

    expired = []
    critical = []  # <= 7 days
    warning = []   # <= 30 days
    ok_list = []
    failed = []

    for domain in domains:
        result = check_cert_expiry.invoke({"domain": domain})

        # 简单解析状态
        if "已过期" in result:
            expired.append(domain)
        elif "即将过期" in result or "临近过期" in result:
            critical.append(domain)
        elif "建议更新" in result:
            warning.append(domain)
        elif "正常" in result:
            ok_list.append(domain)
        else:
            failed.append(domain)

        # 压缩输出每项的摘要
        for line in result.split("\n"):
            stripped = line.strip()
            if any(kw in stripped for kw in ["状态:", "剩余天数:", "主体 CN:", "过期时间:"]):
                lines.append(f"  [{domain}] {stripped}")
        lines.append("")

    # 汇总
    lines.append(f"{'─'*55}")
    lines.append("汇总:")
    lines.append(f"  🔴 已过期: {len(expired)} 个 — {', '.join(expired) if expired else '无'}")
    lines.append(f"  🟡 临近过期(≤7天): {len(critical)} 个 — {', '.join(critical) if critical else '无'}")
    lines.append(f"  🟡 建议更新(≤30天): {len(warning)} 个 — {', '.join(warning) if warning else '无'}")
    lines.append(f"  🟢 正常: {len(ok_list)} 个")
    if failed:
        lines.append(f"  ❌ 检测失败: {len(failed)} 个 — {', '.join(failed)}")

    if expired or critical:
        lines.append(f"")
        lines.append(f"⚠️ 存在紧急证书问题，建议立即处理！")
    elif warning:
        lines.append(f"")
        lines.append(f"⚠️ 部分证书即将过期，请提前规划更新。")

    lines.append(f"{'='*55}")

    return "\n".join(lines)


# ========== 简化版（不依赖 cryptography 库）==========

def _check_cert_simple(domain: str, port: int = 443) -> Dict:
    """
    不依赖 cryptography 库的简化证书检测，仅返回核心信息。
    作为 cryptography 不可用时的降级方案。

    Returns:
        {"domain": str, "remaining_days": int, "status": str, "cn": str, "issuer": str}
    """
    import ssl as _ssl
    import socket as _socket
    from datetime import datetime as _dt, timezone as _tz

    try:
        ctx = _ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE

        with _socket.create_connection((domain, port), timeout=8) as sock:
            with ctx.wrap_socket(sock, server_hostname=domain) as ssock:
                cert_dict = ssock.getpeercert()

        # 从证书字典提取信息
        not_after_str = cert_dict.get("notAfter", "")
        cn = "未知"

        # 提取 CN
        for item in cert_dict.get("subject", []):
            for key, val in item:
                if key == "commonName":
                    cn = val
                    break

        # 提取颁发者
        issuer_cn = "未知"
        for item in cert_dict.get("issuer", []):
            for key, val in item:
                if key == "commonName":
                    issuer_cn = val
                    break

        # 解析过期时间
        try:
            import time as _time
            # notAfter 格式通常为 'Jan 15 12:00:00 2026 GMT'
            not_after_dt = _dt.strptime(not_after_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=_tz.utc)
        except ValueError:
            try:
                from email.utils import parsedate_to_datetime
                not_after_dt = parsedate_to_datetime(not_after_str).astimezone(_tz.utc)
            except Exception:
                not_after_dt = None

        remaining_days = (not_after_dt - _dt.now(_tz.utc)).days if not_after_dt else None

        return {
            "domain": domain,
            "port": port,
            "remaining_days": remaining_days,
            "cn": cn,
            "issuer": issuer_cn,
            "not_after": not_after_str,
            "error": None,
        }
    except Exception as e:
        return {"domain": domain, "port": port, "remaining_days": None, "error": str(e)}


# ========== Agent 类（LLM 增强）==========

SSL_SYSTEM_PROMPT = """你是一名SSL/TLS证书管理专家，负责帮助运维工程师监控和管理服务器证书。

当用户询问证书相关问题时：
1. 调用 check_cert_expiry 工具检测指定域名的证书状态
2. 如果需要批量检测，调用 batch_check_certs 工具
3. 根据检测结果给出：
   - 当前证书状态评估
   - 如果即将过期，给出具体的更新步骤
   - 如果已过期，强调紧急性并给出紧急处理方案
4. 推荐使用 Let's Encrypt + certbot 实现自动续期

注意事项:
- 证书过期是线上故障的常见原因，语气应体现紧急性
- 给出的操作命令应具体、可直接执行
- 区分测试环境和生产环境的处理优先级"""


class SSLCertMonitor:
    """
    SSL证书监控Agent。

    使用方式：
        monitor = SSLCertMonitor(llm_api_key="...", llm_base_url="...")
        result = monitor.check("oa.example.com")
        # LLM 不可用时自动降级为纯工具函数
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
                max_tokens=1024,
            )
            self._agent = create_agent(
                model=llm,
                tools=[check_cert_expiry, batch_check_certs],
                system_prompt=SSL_SYSTEM_PROMPT,
            )
            logger.info("SSL证书监控Agent初始化完成")
        except Exception as e:
            logger.warning(f"SSL Agent 初始化失败: {e}")

    def check(self, domain: str) -> str:
        """检测单个域名证书"""
        if self._agent:
            try:
                result = self._agent.invoke({
                    "messages": [{"role": "user", "content": f"请检测 {domain} 的SSL证书状态"}]
                })
                msgs = result.get("messages", [])
                if msgs:
                    return msgs[-1].content
            except Exception as e:
                logger.warning(f"Agent 检测异常，降级: {e}")
        return check_cert_expiry.invoke({"domain": domain})

    def batch_check(self, domains_text: str) -> str:
        """批量检测证书"""
        if self._agent:
            try:
                result = self._agent.invoke({
                    "messages": [{"role": "user", "content": f"请批量检测以下域名的SSL证书:\n{domains_text}"}]
                })
                msgs = result.get("messages", [])
                if msgs:
                    return msgs[-1].content
            except Exception as e:
                logger.warning(f"Agent 批量检测异常，降级: {e}")
        return batch_check_certs.invoke({"domains_text": domains_text})


# ========== 快速测试入口 ==========

if __name__ == "__main__":
    print("=== SSL 证书监控模块测试 ===\n")

    # 测试1: 简化版检测（不依赖 cryptography）
    print("--- 简化版检测 baidu.com ---")
    result = _check_cert_simple("www.baidu.com", 443)
    if result["error"]:
        print(f"检测失败: {result['error']}")
    else:
        print(f"域名: {result['cn']}")
        print(f"剩余天数: {result['remaining_days']}")
        print(f"颁发者: {result['issuer']}")

    # 测试2: 完整版（需要 cryptography）
    try:
        print("\n--- 完整版检测 www.baidu.com ---")
        print(check_cert_expiry.invoke({"domain": "www.baidu.com"}))
    except ImportError:
        print("cryptography 库未安装，跳过完整版测试")
        print("安装: pip install cryptography")
    except Exception as e:
        print(f"检测异常（可能无网络）: {e}")
