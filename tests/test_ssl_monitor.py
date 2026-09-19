"""Use real generated X.509 certificates and mock only the network transport."""

import ipaddress
import socket
import ssl
import sys
from datetime import datetime, timedelta, timezone
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from agents import ssl_monitor as monitor


def certificate_bytes(days):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "oa.example.com")])
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test CA")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(100)
        .not_valid_before(now - timedelta(days=90))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("oa.example.com")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.DER)


@pytest.mark.parametrize("days,status", [
    (-1, "已过期"), (1, "即将过期"), (5, "临近过期"), (20, "建议更新"), (60, "正常"),
])
def test_certificate_status_and_identity_from_real_der(days, status, monkeypatch):
    der = certificate_bytes(days)
    tls = MagicMock()
    tls.getpeercert.side_effect = lambda binary_form=False: der if binary_form else {}
    context = MagicMock()
    context.wrap_socket.return_value.__enter__.return_value = tls
    connect = MagicMock()
    monkeypatch.setattr(monitor.ssl, "create_default_context", Mock(return_value=context))
    monkeypatch.setattr(monitor.socket, "create_connection", connect)
    report = monitor.check_cert_expiry.invoke({"domain": "oa.example.com:8443"})
    assert "状态: " in report and status in report
    assert "主体 CN: oa.example.com" in report
    assert "颁发者: Test CA" in report
    assert "SAN 列表: oa.example.com" in report
    connect.assert_called_once_with(("oa.example.com", 8443), timeout=10)


@pytest.mark.parametrize("error,expected", [
    (socket.timeout(), "超时"),
    (socket.gaierror(), "DNS 解析失败"),
    (ConnectionRefusedError(), "连接被拒绝"),
    (ssl.SSLError("handshake"), "SSL 握手失败"),
])
def test_certificate_transport_errors_are_explained(error, expected, monkeypatch):
    monkeypatch.setattr(monitor.socket, "create_connection", Mock(side_effect=error))
    assert expected in monitor.check_cert_expiry.invoke({"domain": "oa.example.com"})


@pytest.mark.parametrize("domain", ["", " ", "oa.example.com:abc"])
def test_invalid_certificate_request_does_not_connect(domain, monkeypatch) -> None:
    connect = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(monitor.socket, "create_connection", connect)
    assert "错误" in monitor.check_cert_expiry.invoke({"domain": domain})
    connect.assert_not_called()


# ========== 追加：证书名/SAN 分支、降级路径、批量检测与 Agent ==========


def build_certificate(days, subject_attrs, issuer_attrs, san_names=None):
    """构造一张真实 DER 证书，用于覆盖证书解析的各个分支。"""
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name(subject_attrs))
        .issuer_name(x509.Name(issuer_attrs))
        .public_key(key.public_key())
        .serial_number(0x4F41)
        .not_valid_before(now - timedelta(days=7))
        .not_valid_after(now + timedelta(days=days))
    )
    if san_names:
        builder = builder.add_extension(x509.SubjectAlternativeName(san_names), critical=False)
    return builder.sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.DER)


def _install_tls_transport(monkeypatch, der_bytes) -> None:
    tls = MagicMock()
    tls.getpeercert.side_effect = lambda binary_form=False: der_bytes if binary_form else {}
    context = MagicMock()
    context.wrap_socket.return_value.__enter__.return_value = tls
    monkeypatch.setattr(monitor.ssl, "create_default_context", Mock(return_value=context))
    monkeypatch.setattr(monitor.socket, "create_connection", MagicMock())


def test_certificate_without_common_names_stays_unknown(monkeypatch) -> None:
    der = build_certificate(
        40,
        subject_attrs=[x509.NameAttribute(NameOID.ORGANIZATION_NAME, "运维部")],
        issuer_attrs=[x509.NameAttribute(NameOID.ORGANIZATION_NAME, "某CA机构")],
    )
    _install_tls_transport(monkeypatch, der)

    report = monitor.check_cert_expiry.invoke({"domain": "oa.example.com"})

    assert "主体 CN: 未知" in report
    assert "颁发者: 未知" in report
    assert "🟢 正常" in report


def test_certificate_scans_all_name_attributes(monkeypatch) -> None:
    der = build_certificate(
        20,
        subject_attrs=[
            x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
            x509.NameAttribute(NameOID.COMMON_NAME, "oa.example.com"),
        ],
        issuer_attrs=[
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "某CA机构"),
            x509.NameAttribute(NameOID.COMMON_NAME, "Test Root CA"),
        ],
    )
    _install_tls_transport(monkeypatch, der)

    report = monitor.check_cert_expiry.invoke({"domain": "oa.example.com"})

    assert "主体 CN: oa.example.com" in report
    assert "颁发者: Test Root CA" in report
    assert "建议更新" in report


def test_certificate_san_list_keeps_only_dns_names(monkeypatch) -> None:
    der = build_certificate(
        60,
        subject_attrs=[x509.NameAttribute(NameOID.COMMON_NAME, "oa.example.com")],
        issuer_attrs=[x509.NameAttribute(NameOID.COMMON_NAME, "Test CA")],
        san_names=[
            x509.DNSName("oa.example.com"),
            x509.IPAddress(ipaddress.IPv4Address("10.20.30.40")),
            x509.RFC822Name("ops@example.com"),
        ],
    )
    _install_tls_transport(monkeypatch, der)

    report = monitor.check_cert_expiry.invoke({"domain": "oa.example.com"})

    assert "SAN 列表: oa.example.com" in report
    assert "10.20.30.40" not in report
    assert "ops@example.com" not in report


def test_certificate_without_san_extension_is_tolerated(monkeypatch) -> None:
    der = build_certificate(
        60,
        subject_attrs=[x509.NameAttribute(NameOID.COMMON_NAME, "oa.example.com")],
        issuer_attrs=[x509.NameAttribute(NameOID.COMMON_NAME, "Test CA")],
    )
    _install_tls_transport(monkeypatch, der)

    report = monitor.check_cert_expiry.invoke({"domain": "oa.example.com"})

    assert "主体 CN: oa.example.com" in report
    assert "SAN 列表" not in report


def test_certificate_truncates_long_san_lists(monkeypatch) -> None:
    san_names = [x509.DNSName(f"oa{index}.example.com") for index in range(12)]
    der = build_certificate(
        60,
        subject_attrs=[x509.NameAttribute(NameOID.COMMON_NAME, "oa.example.com")],
        issuer_attrs=[x509.NameAttribute(NameOID.COMMON_NAME, "Test CA")],
        san_names=san_names,
    )
    _install_tls_transport(monkeypatch, der)

    report = monitor.check_cert_expiry.invoke({"domain": "oa.example.com"})

    assert "共 12 个" in report
    assert "oa11.example.com" not in report


def test_certificate_check_reports_unexpected_errors(monkeypatch) -> None:
    monkeypatch.setattr(monitor.socket, "create_connection", Mock(side_effect=ValueError("socket 状态异常")))

    report = monitor.check_cert_expiry.invoke({"domain": "oa.example.com"})

    assert "[错误] 检测失败: socket 状态异常" in report


def _install_degraded_transport(monkeypatch, simple_result) -> None:
    _install_tls_transport(monkeypatch, b"fake-der")
    monkeypatch.setattr(monitor, "HAS_CRYPTOGRAPHY", False)
    monkeypatch.setattr(monitor, "_check_cert_simple", Mock(return_value=simple_result))


def test_degraded_mode_reports_certificate_without_cryptography(monkeypatch) -> None:
    _install_degraded_transport(
        monkeypatch,
        {
            "domain": "oa.example.com",
            "port": 443,
            "cn": "oa.example.com",
            "issuer": "Test CA",
            "remaining_days": 15,
            "not_after": "Jan 15 12:00:00 2027 GMT",
            "error": None,
        },
    )

    report = monitor.check_cert_expiry.invoke({"domain": "oa.example.com"})

    assert "主体 CN: oa.example.com" in report
    assert "颁发者: Test CA" in report
    assert "剩余天数: 15 天" in report
    assert "序列号: 未知（需安装 cryptography）" in report
    assert "生效时间: 未知" in report
    assert "建议更新" in report


def test_degraded_mode_propagates_detection_errors(monkeypatch) -> None:
    _install_degraded_transport(monkeypatch, {"error": "连接被拒绝"})

    assert (
        monitor.check_cert_expiry.invoke({"domain": "oa.example.com"})
        == "[错误] 证书检测失败: 连接被拒绝"
    )


def test_degraded_mode_rejects_unparsable_expiry(monkeypatch) -> None:
    _install_degraded_transport(
        monkeypatch, {"error": None, "remaining_days": None, "not_after": "not-a-date"}
    )

    report = monitor.check_cert_expiry.invoke({"domain": "oa.example.com"})

    assert "[错误] 无法解析证书过期时间: not-a-date" in report


def _install_fake_cert_tool(monkeypatch, results) -> None:
    def invoke(payload):
        return results[payload["domain"]]

    monkeypatch.setattr(monitor, "check_cert_expiry", SimpleNamespace(invoke=invoke))
    monkeypatch.setattr(monitor, "config", SimpleNamespace(get=lambda path, default=None: []))


def test_batch_check_requires_domain_list(monkeypatch) -> None:
    assert "[提示] 请提供域名列表" in monitor.batch_check_certs.invoke({"domains_text": "  \n "})


def test_batch_check_summarizes_statuses_and_failures(monkeypatch) -> None:
    results = {
        "oa1.example.com": "状态: 🔴 已过期\n  剩余天数: -3 天",
        "oa2.example.com": "状态: 🟡 临近过期\n  剩余天数: 5 天\n  主体 CN: oa2.example.com\n  过期时间: 2026-10-01",
        "oa3.example.com": "状态: 🟡 建议更新",
        "oa4.example.com": "状态: 🟢 正常",
        "oa5.example.com": "[错误] DNS 解析失败: oa5.example.com",
    }
    _install_fake_cert_tool(monkeypatch, results)

    report = monitor.batch_check_certs.invoke({"domains_text": "\n".join(results)})

    assert "检测数量: 5 个域名" in report
    assert "已过期: 1 个 — oa1.example.com" in report
    assert "临近过期(≤7天): 1 个 — oa2.example.com" in report
    assert "建议更新(≤30天): 1 个 — oa3.example.com" in report
    assert "🟢 正常: 1 个" in report
    assert "检测失败: 1 个 — oa5.example.com" in report
    assert "存在紧急证书问题" in report
    assert "  [oa2.example.com] 主体 CN: oa2.example.com" in report
    assert "  [oa4.example.com] 状态: 🟢 正常" in report


def test_batch_check_warns_when_certificates_expire_soon(monkeypatch) -> None:
    _install_fake_cert_tool(monkeypatch, {"oa1.example.com": "状态: 🟡 建议更新"})

    report = monitor.batch_check_certs.invoke({"domains_text": "oa1.example.com"})

    assert "部分证书即将过期，请提前规划更新。" in report
    assert "存在紧急证书问题" not in report


def test_batch_check_merges_configured_domains(monkeypatch) -> None:
    results = {"user.example.com": "状态: 🟢 正常", "ops.example.com": "状态: 🟢 正常"}
    _install_fake_cert_tool(monkeypatch, results)
    monkeypatch.setattr(
        monitor,
        "config",
        SimpleNamespace(
            get=lambda path, default=None: ["ops.example.com", "user.example.com"]
            if path == "ssl_monitor.domains"
            else default
        ),
    )

    report = monitor.batch_check_certs.invoke({"domains_text": "user.example.com\n"})

    assert "检测数量: 2 个域名" in report
    assert "🟢 正常: 2 个" in report


def _install_simple_ssl_stack(monkeypatch, cert_dict=None, error=None) -> None:
    if error is not None:
        monkeypatch.setattr(monitor.socket, "create_connection", Mock(side_effect=error))
        return
    ssock = MagicMock()
    ssock.getpeercert.return_value = cert_dict
    context = MagicMock()
    context.wrap_socket.return_value.__enter__.return_value = ssock
    monkeypatch.setattr(monitor.ssl, "create_default_context", Mock(return_value=context))
    monkeypatch.setattr(monitor.socket, "create_connection", MagicMock())


def test_simple_cert_check_parses_common_name_and_expiry(monkeypatch) -> None:
    not_after = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%b %d %H:%M:%S %Y") + " GMT"
    _install_simple_ssl_stack(
        monkeypatch,
        cert_dict={
            "notAfter": not_after,
            "subject": [[("organizationName", "运维部"), ("commonName", "oa.example.com")]],
            "issuer": [[("commonName", "Test CA")]],
        },
    )

    result = monitor._check_cert_simple("oa.example.com")

    assert result["error"] is None
    assert result["cn"] == "oa.example.com"
    assert result["issuer"] == "Test CA"
    assert 28 <= result["remaining_days"] <= 30


def test_simple_cert_check_parses_rfc2822_expiry(monkeypatch) -> None:
    not_after = (datetime.now(timezone.utc) + timedelta(days=10)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    _install_simple_ssl_stack(
        monkeypatch,
        cert_dict={
            "notAfter": not_after,
            "subject": [[("commonName", "oa.example.com")]],
            "issuer": [],
        },
    )

    result = monitor._check_cert_simple("oa.example.com")

    assert result["error"] is None
    assert 8 <= result["remaining_days"] <= 10
    assert result["issuer"] == "未知"


def test_simple_cert_check_returns_none_for_unparsable_expiry(monkeypatch) -> None:
    _install_simple_ssl_stack(
        monkeypatch, cert_dict={"notAfter": "not-a-date", "subject": [], "issuer": []}
    )

    result = monitor._check_cert_simple("oa.example.com")

    assert result["error"] is None
    assert result["remaining_days"] is None
    assert result["cn"] == "未知"


def test_simple_cert_check_captures_transport_failure(monkeypatch) -> None:
    _install_simple_ssl_stack(monkeypatch, error=ConnectionRefusedError("拒绝连接"))

    result = monitor._check_cert_simple("oa.example.com", 8443)

    assert result["remaining_days"] is None
    assert result["port"] == 8443
    assert result["error"] is not None and "拒绝连接" in result["error"]


def test_simple_cert_check_ignores_non_common_name_attributes(monkeypatch) -> None:
    not_after = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%b %d %H:%M:%S %Y") + " GMT"
    _install_simple_ssl_stack(
        monkeypatch,
        cert_dict={
            "notAfter": not_after,
            "subject": [[("organizationName", "运维部")]],
            "issuer": [
                [("organizationName", "某CA机构"), ("commonName", "Test CA")],
                [("organizationName", "备用CA")],
            ],
        },
    )

    result = monitor._check_cert_simple("oa.example.com")

    assert result["cn"] == "未知"
    assert result["issuer"] == "Test CA"
    assert 3 <= result["remaining_days"] <= 5


class _FakeLlmMessage:
    def __init__(self, content: str) -> None:
        self.content = content


def _install_fake_llm_modules(monkeypatch, build_agent) -> dict:
    """注册假的 langchain_openai / langchain.agents，避免真实初始化 LLM。"""
    captured: dict = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs) -> None:
            captured["llm_kwargs"] = kwargs

    def fake_create_agent(**kwargs):
        captured["agent_kwargs"] = kwargs
        return build_agent()

    openai_module = ModuleType("langchain_openai")
    setattr(openai_module, "ChatOpenAI", FakeChatOpenAI)
    agents_module = ModuleType("langchain.agents")
    setattr(agents_module, "create_agent", fake_create_agent)
    monkeypatch.setitem(sys.modules, "langchain_openai", openai_module)
    monkeypatch.setitem(sys.modules, "langchain.agents", agents_module)
    return captured


def test_ssl_monitor_uses_llm_agent_when_configured(monkeypatch) -> None:
    expected_tools = [monitor.check_cert_expiry, monitor.batch_check_certs]
    fake_agent = SimpleNamespace(
        invoke=Mock(return_value={"messages": [_FakeLlmMessage("证书还有 12 天到期，请尽快更换")]})
    )
    captured = _install_fake_llm_modules(monkeypatch, lambda: fake_agent)

    cert_monitor = monitor.SSLCertMonitor(llm_api_key="sk-oa-test")

    assert cert_monitor.check("oa.example.com") == "证书还有 12 天到期，请尽快更换"
    assert cert_monitor.batch_check("oa.example.com") == "证书还有 12 天到期，请尽快更换"
    assert captured["agent_kwargs"]["tools"] == expected_tools
    assert captured["llm_kwargs"]["model"] == "deepseek-chat"


@pytest.mark.parametrize("api_key", ["", "ollama", "your-api-key-here"])
def test_ssl_monitor_skips_agent_for_placeholder_keys(api_key, monkeypatch) -> None:
    check_tool = SimpleNamespace(invoke=Mock(return_value="[工具] 证书正常"))
    batch_tool = SimpleNamespace(invoke=Mock(return_value="[工具] 批量正常"))
    monkeypatch.setattr(monitor, "check_cert_expiry", check_tool)
    monkeypatch.setattr(monitor, "batch_check_certs", batch_tool)

    cert_monitor = monitor.SSLCertMonitor(llm_api_key=api_key)

    assert cert_monitor._agent is None
    assert cert_monitor.check("oa.example.com") == "[工具] 证书正常"
    assert cert_monitor.batch_check("oa.example.com") == "[工具] 批量正常"
    check_tool.invoke.assert_called_once_with({"domain": "oa.example.com"})
    batch_tool.invoke.assert_called_once_with({"domains_text": "oa.example.com"})


def test_ssl_monitor_survives_agent_init_failure(monkeypatch) -> None:
    def boom():
        raise RuntimeError("cannot create agent")

    _install_fake_llm_modules(monkeypatch, boom)
    monkeypatch.setattr(monitor, "check_cert_expiry", SimpleNamespace(invoke=Mock(return_value="[降级] 证书正常")))

    cert_monitor = monitor.SSLCertMonitor(llm_api_key="sk-oa-test")

    assert cert_monitor._agent is None
    assert cert_monitor.check("oa.example.com") == "[降级] 证书正常"


def test_ssl_monitor_falls_back_when_agent_call_fails(monkeypatch) -> None:
    _install_fake_llm_modules(
        monkeypatch, lambda: SimpleNamespace(invoke=Mock(side_effect=RuntimeError("模型超时")))
    )
    monkeypatch.setattr(monitor, "check_cert_expiry", SimpleNamespace(invoke=Mock(return_value="[降级] 单域名结果")))
    monkeypatch.setattr(monitor, "batch_check_certs", SimpleNamespace(invoke=Mock(return_value="[降级] 批量结果")))

    cert_monitor = monitor.SSLCertMonitor(llm_api_key="sk-oa-test")

    assert cert_monitor.check("oa.example.com") == "[降级] 单域名结果"
    assert cert_monitor.batch_check("oa.example.com\nwww.example.com") == "[降级] 批量结果"


def test_ssl_monitor_falls_back_when_agent_returns_no_messages(monkeypatch) -> None:
    _install_fake_llm_modules(
        monkeypatch, lambda: SimpleNamespace(invoke=Mock(return_value={"messages": []}))
    )
    monkeypatch.setattr(monitor, "check_cert_expiry", SimpleNamespace(invoke=Mock(return_value="[降级] 单域名结果")))
    monkeypatch.setattr(monitor, "batch_check_certs", SimpleNamespace(invoke=Mock(return_value="[降级] 批量结果")))

    cert_monitor = monitor.SSLCertMonitor(llm_api_key="sk-oa-test")

    assert cert_monitor.check("oa.example.com") == "[降级] 单域名结果"
    assert cert_monitor.batch_check("oa.example.com") == "[降级] 批量结果"
