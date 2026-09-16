"""Network tools: no real DNS, sockets or OS commands are used in these tests."""

import socket
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agents import network_diag as network


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "oa.example.com", "oa-server"])
def test_valid_hosts(host):
    assert network._is_safe_host(host)


@pytest.mark.parametrize("host", [
    "-n", "--help", "host;whoami", "host\ncmd", "", "x" * 254,
    ".", "foo..bar", "x" * 64 + ".com", "-label.example", "label-.example",
])
def test_reject_shell_and_option_injection(host):
    assert not network._is_safe_host(host)


@pytest.mark.parametrize("tool", [network.ping_host, network.traceroute_host])
def test_invalid_target_never_launches_command(tool, monkeypatch):
    runner = Mock(side_effect=AssertionError("must not launch a process"))
    monkeypatch.setattr(network, "_run_command", runner)
    assert "非法" in tool.invoke({"host": "--help"})
    runner.assert_not_called()


@pytest.mark.parametrize("domain", ["host;whoami", "foo..bar", "."])
def test_dns_validates_before_resolving(monkeypatch, domain):
    resolver = Mock(side_effect=AssertionError("must not query DNS"))
    monkeypatch.setattr(network.socket, "getaddrinfo", resolver)
    assert "非法" in network.dns_resolve.invoke({"domain": domain})
    resolver.assert_not_called()


@pytest.mark.parametrize("target", ["localhost:0", "localhost:65536", "localhost:-1", ":80", "foo..bar:80"])
def test_invalid_port_or_empty_host_never_opens_socket(target, monkeypatch):
    factory = Mock(side_effect=AssertionError("must not create a socket"))
    monkeypatch.setattr(network.socket, "socket", factory)
    assert "错误" in network.check_tcp_port.invoke({"host_port": target})
    factory.assert_not_called()


@pytest.mark.parametrize("windows", [True, False])
def test_ping_uses_argument_list_and_reports_latency(windows, monkeypatch):
    monkeypatch.setattr(network, "IS_WINDOWS", windows)
    output = (
        "0% 丢失 最短 = 1ms 最长 = 3ms 平均 = 2ms"
        if windows else "0% packet loss\nrtt min/avg/max/mdev = 1/2/3/0.1 ms"
    )
    runner = Mock(return_value=output)
    monkeypatch.setattr(network, "_run_command", runner)
    report = network.ping_host.invoke({"host": "oa.example.com"})
    assert "网络正常" in report
    assert "平均=2ms" in report
    args = runner.call_args.args[0]
    assert isinstance(args, list)
    assert args[-1] == "oa.example.com"
    assert ("-n" if windows else "-c") in args


def test_ping_full_loss_includes_troubleshooting(monkeypatch):
    monkeypatch.setattr(network, "IS_WINDOWS", False)
    monkeypatch.setattr(network, "_run_command", Mock(return_value="100% packet loss"))
    report = network.ping_host.invoke({"host": "oa.example.com"})
    assert "目标不可达" in report
    assert "排查建议" in report


def test_command_timeout_is_reported(monkeypatch):
    run = Mock(side_effect=subprocess.TimeoutExpired(["ping"], 1))
    monkeypatch.setattr(network.subprocess, "run", run)
    assert network._run_command(["ping", "localhost"], timeout=1) == "命令执行超时"
    assert run.call_args.kwargs["shell"] is False


def test_command_stderr_is_returned(monkeypatch):
    monkeypatch.setattr(
        network.subprocess, "run", Mock(return_value=SimpleNamespace(stdout="", stderr="denied"))
    )
    assert network._run_command(["ping", "localhost"]) == "denied"


@pytest.mark.parametrize("result_code,expected", [(0, "端口开放"), (10061, "端口不可达")])
def test_tcp_closes_socket_after_result(result_code, expected, monkeypatch):
    client = Mock()
    client.connect_ex.return_value = result_code
    client.recv.return_value = b"test service"
    monkeypatch.setattr(network.socket, "socket", Mock(return_value=client))
    assert expected in network.check_tcp_port.invoke({"host_port": "localhost:8080"})
    client.connect_ex.assert_called_once_with(("localhost", 8080))
    client.close.assert_called_once()


def test_tcp_closes_socket_after_timeout(monkeypatch):
    client = Mock()
    client.connect_ex.side_effect = socket.timeout()
    monkeypatch.setattr(network.socket, "socket", Mock(return_value=client))
    assert "超时" in network.check_tcp_port.invoke({"host_port": "localhost:8080"})
    client.close.assert_called_once()
