"""Use real generated X.509 certificates and mock only the network transport."""

import socket
import ssl
from datetime import datetime, timedelta, timezone
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
def test_invalid_certificate_request_does_not_connect(domain, monkeypatch):
    connect = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(monitor.socket, "create_connection", connect)
    assert "错误" in monitor.check_cert_expiry.invoke({"domain": domain})
    connect.assert_not_called()
