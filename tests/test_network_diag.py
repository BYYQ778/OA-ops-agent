"""Network tools: no real DNS, sockets or OS commands are used in these tests."""

import socket
import ssl
import subprocess
import sys
import urllib.error
from email.message import Message
from types import ModuleType, SimpleNamespace
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


# ========== 追加：命令执行、解析分支与 Agent 降级 ==========


def test_run_command_reports_unexpected_exception(monkeypatch) -> None:
    monkeypatch.setattr(network.subprocess, "run", Mock(side_effect=OSError("拒绝访问")))

    assert network._run_command(["ping", "localhost"]) == "命令执行失败: 拒绝访问"


def test_ping_rejects_blank_host(monkeypatch) -> None:
    runner = Mock(side_effect=AssertionError("must not launch a process"))
    monkeypatch.setattr(network, "_run_command", runner)

    assert network.ping_host.invoke({"host": "   "}) == "[错误] 请输入目标 IP 或域名"
    runner.assert_not_called()


def test_ping_reports_command_failure(monkeypatch) -> None:
    monkeypatch.setattr(network, "_run_command", Mock(return_value="命令执行超时"))

    report = network.ping_host.invoke({"host": "oa.example.com"})

    assert "[错误] Ping 执行失败: 命令执行超时" in report


@pytest.mark.parametrize(
    "output,expected",
    [
        ("25% 丢失 最短 = 1ms 最长 = 3ms 平均 = 2ms", "🟡 部分丢包 (25%)"),
        ("80% 丢失 最短 = 5ms 最长 = 9ms 平均 = 7ms", "🔴 严重丢包 (80%)"),
    ],
)
def test_ping_windows_loss_levels(output, expected, monkeypatch) -> None:
    monkeypatch.setattr(network, "IS_WINDOWS", True)
    monkeypatch.setattr(network, "_run_command", Mock(return_value=output))

    report = network.ping_host.invoke({"host": "oa.example.com"})

    assert expected in report
    assert "延迟: " in report


def test_ping_without_loss_percentage_reports_unreachable(monkeypatch) -> None:
    monkeypatch.setattr(network, "IS_WINDOWS", True)
    monkeypatch.setattr(network, "_run_command", Mock(return_value="Ping 请求找不到主机 oa.example.com"))

    report = network.ping_host.invoke({"host": "oa.example.com"})

    assert "ping 请求无响应" in report
    assert "延迟" not in report


def test_ping_linux_uses_average_fallback(monkeypatch) -> None:
    monkeypatch.setattr(network, "IS_WINDOWS", False)
    monkeypatch.setattr(network, "_run_command", Mock(return_value="0% packet loss\navg = 3.5 ms"))

    report = network.ping_host.invoke({"host": "oa.example.com"})

    assert "🟢 网络正常" in report
    assert "延迟: 平均=3.5ms" in report


def test_tcp_port_requires_host_port_format(monkeypatch) -> None:
    factory = Mock(side_effect=AssertionError("must not create a socket"))
    monkeypatch.setattr(network.socket, "socket", factory)

    assert "请按 host:port 格式输入" in network.check_tcp_port.invoke({"host_port": "localhost"})
    factory.assert_not_called()


def test_tcp_port_rejects_non_numeric_port(monkeypatch) -> None:
    factory = Mock(side_effect=AssertionError("must not create a socket"))
    monkeypatch.setattr(network.socket, "socket", factory)

    assert "端口号无效: http" in network.check_tcp_port.invoke({"host_port": "localhost:http"})
    factory.assert_not_called()


def test_tcp_banner_probe_failure_is_tolerated(monkeypatch) -> None:
    client = Mock()
    client.connect_ex.return_value = 0
    client.recv.side_effect = socket.timeout()
    monkeypatch.setattr(network.socket, "socket", Mock(return_value=client))

    report = network.check_tcp_port.invoke({"host_port": "localhost:8080"})

    assert "状态: 🟢 端口开放" in report
    assert "服务Banner" not in report
    client.close.assert_called_once()


@pytest.mark.parametrize(
    "error,expected",
    [
        (socket.gaierror("解析失败"), "DNS 解析失败: oa-db01"),
        (RuntimeError("句柄异常"), "端口检测异常: 句柄异常"),
    ],
)
def test_tcp_connection_errors_are_explained(error, expected, monkeypatch) -> None:
    client = Mock()
    client.connect_ex.side_effect = error
    monkeypatch.setattr(network.socket, "socket", Mock(return_value=client))

    assert expected in network.check_tcp_port.invoke({"host_port": "oa-db01:3306"})
    client.close.assert_called_once()


def test_dns_resolve_rejects_blank_domain(monkeypatch) -> None:
    resolver = Mock(side_effect=AssertionError("must not query DNS"))
    monkeypatch.setattr(network.socket, "getaddrinfo", resolver)

    assert network.dns_resolve.invoke({"domain": "  "}) == "[错误] 请输入域名"
    resolver.assert_not_called()


def _install_fake_dns(monkeypatch, ipv4=None, ipv6=None, error_for=None) -> None:
    def fake_getaddrinfo(domain, port, family=0, *args, **kwargs):
        if error_for is not None and family == error_for:
            raise socket.gaierror(11001, "getaddrinfo failed")
        addresses = ipv4 if family == socket.AF_INET else ipv6 if family == socket.AF_INET6 else []
        return [(family, socket.SOCK_STREAM, 6, "", (address, 0)) for address in (addresses or [])]

    monkeypatch.setattr(network.socket, "getaddrinfo", fake_getaddrinfo)


def test_dns_resolve_reports_all_record_types(monkeypatch) -> None:
    _install_fake_dns(
        monkeypatch,
        ipv4=["192.168.1.10", "192.168.1.11"],
        ipv6=["2408:8207::1", "2408:8207::2", "2408:8207::3", "2408:8207::4"],
    )

    def runner(args, timeout=15):
        if args[1] == "-type=CNAME":
            return "oa.example.com\tcanonical name = cdn.example.com"
        if args[1] == "-type=MX":
            return (
                "oa.example.com\tMX preference = 20, mail exchanger = mx2.example.com\n"
                "oa.example.com\tMX preference = 10, mail exchanger = mx1.example.com"
            )
        return ""

    monkeypatch.setattr(network, "_run_command", Mock(side_effect=runner))

    report = network.dns_resolve.invoke({"domain": "oa.example.com"})

    assert "A 记录 (IPv4): 2 个" in report
    assert "→ 192.168.1.10" in report
    assert "AAAA 记录 (IPv6): 4 个" in report
    assert "共 4 个" in report
    assert "CNAME 记录: cdn.example.com" in report
    assert "MX 记录 (邮件): 2 条" in report
    assert "→ mx1.example.com (优先级: 10)" in report
    assert report.index("mx1.example.com") < report.index("mx2.example.com")


def test_dns_resolve_falls_back_to_raw_cname_and_mx_lines(monkeypatch) -> None:
    _install_fake_dns(monkeypatch, ipv4=["192.168.1.10"], ipv6=[])
    monkeypatch.setattr(network, "_run_command", Mock(return_value="alias.oa.example.com."))

    report = network.dns_resolve.invoke({"domain": "oa.example.com"})

    assert "CNAME 记录: alias.oa.example.com" in report
    assert "→ alias.oa.example.com." in report


def test_dns_resolve_ignores_lookup_failures(monkeypatch) -> None:
    _install_fake_dns(monkeypatch, error_for=socket.AF_INET)
    monkeypatch.setattr(network, "_run_command", Mock(return_value="** server can't find oa.example.com: NXDOMAIN"))

    report = network.dns_resolve.invoke({"domain": "oa.example.com"})

    assert "A 记录: 解析失败（域名不存在？）" in report
    assert "CNAME" not in report
    assert "MX" not in report


def test_dns_resolve_tolerates_missing_ipv6(monkeypatch) -> None:
    _install_fake_dns(monkeypatch, ipv4=["192.168.1.10"], error_for=socket.AF_INET6)
    monkeypatch.setattr(network, "_run_command", Mock(return_value=""))

    report = network.dns_resolve.invoke({"domain": "oa.example.com"})

    assert "A 记录 (IPv4): 1 个" in report
    assert "AAAA" not in report


def test_dns_resolve_lists_short_ipv6_sets_without_truncation(monkeypatch) -> None:
    _install_fake_dns(monkeypatch, ipv4=[], ipv6=["2408:8207::1", "2408:8207::2"])
    monkeypatch.setattr(network, "_run_command", Mock(return_value=""))

    report = network.dns_resolve.invoke({"domain": "oa.example.com"})

    assert "AAAA 记录 (IPv6): 2 个" in report
    assert "→ 2408:8207::2" in report
    assert "共" not in report


def test_dns_resolve_skips_unparsable_cname_and_mx_output(monkeypatch) -> None:
    _install_fake_dns(monkeypatch, ipv4=["192.168.1.10"], ipv6=[])

    def runner(args, timeout=15):
        if args[1] == "-type=CNAME":
            return "canonical zone data without a canonical name"
        return "mail exchanger information unavailable"

    monkeypatch.setattr(network, "_run_command", Mock(side_effect=runner))

    report = network.dns_resolve.invoke({"domain": "oa.example.com"})

    assert "CNAME" not in report
    assert "MX" not in report


def test_dns_resolve_skips_blank_cname_and_mx_lines(monkeypatch) -> None:
    _install_fake_dns(monkeypatch, ipv4=["192.168.1.10"], ipv6=[])

    def runner(args, timeout=15):
        if args[1] == "-type=CNAME":
            return "."
        return "mx1.example.com\n\nmx2.example.com"

    monkeypatch.setattr(network, "_run_command", Mock(side_effect=runner))

    report = network.dns_resolve.invoke({"domain": "oa.example.com"})

    assert "CNAME" not in report
    assert "→ mx1.example.com" in report
    assert "→ mx2.example.com" in report
    assert "→ \n" not in report


@pytest.mark.parametrize("windows,expect_command", [(True, "tracert"), (False, "traceroute")])
def test_traceroute_uses_platform_command_and_embeds_output(windows, expect_command, monkeypatch) -> None:
    monkeypatch.setattr(network, "IS_WINDOWS", windows)
    runner = Mock(return_value="  1    <1 ms    1 ms  gateway  [192.168.1.1]")
    monkeypatch.setattr(network, "_run_command", runner)

    report = network.traceroute_host.invoke({"host": "oa.example.com"})

    assert runner.call_args.args[0][0] == expect_command
    assert runner.call_args.kwargs["timeout"] == 35
    assert "192.168.1.1" in report
    assert "💡 说明:" in report


def test_traceroute_reports_empty_output(monkeypatch) -> None:
    monkeypatch.setattr(network, "_run_command", Mock(return_value=""))

    report = network.traceroute_host.invoke({"host": "oa.example.com"})

    assert "[提示] 无输出，可能需要管理员/root 权限" in report


def test_traceroute_rejects_blank_host(monkeypatch) -> None:
    runner = Mock(side_effect=AssertionError("must not launch a process"))
    monkeypatch.setattr(network, "_run_command", runner)

    assert network.traceroute_host.invoke({"host": ""}) == "[错误] 请输入目标 IP 或域名"
    runner.assert_not_called()


class _FakeHttpResponse:
    def __init__(self, status: int, headers: dict) -> None:
        self.status = status
        self.headers = headers

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False


def _install_fake_urlopen(monkeypatch, response=None, error=None) -> Mock:
    opener = Mock(return_value=response) if error is None else Mock(side_effect=error)
    monkeypatch.setattr(network.urllib.request, "urlopen", opener)
    return opener


def test_http_health_check_summarizes_healthy_response(monkeypatch) -> None:
    response = _FakeHttpResponse(
        200, {"Content-Type": "text/html", "Content-Length": "1024", "Server": "nginx/1.24.0"}
    )
    opener = _install_fake_urlopen(monkeypatch, response=response)

    report = network.http_health_check.invoke({"url": "oa.example.com"})

    assert "URL: https://oa.example.com" in report
    assert "🟢 正常" in report
    assert "HTTP 状态码: 200" in report
    assert "服务器: nginx/1.24.0" in report
    assert "Content-Type: text/html" in report
    assert "Content-Length: 1024" in report
    assert "响应时间超过 2 秒" not in report
    request = opener.call_args.args[0]
    assert request.full_url == "https://oa.example.com"
    assert request.get_header("User-agent") == "OA-Ops-Agent/2.0 Network Health Check"


def test_http_health_check_reports_missing_headers(monkeypatch) -> None:
    _install_fake_urlopen(monkeypatch, response=_FakeHttpResponse(204, {}))

    report = network.http_health_check.invoke({"url": "https://oa.example.com"})

    assert "服务器: 未知" in report
    assert "Content-Length: 未知" in report


def test_http_health_check_marks_redirect(monkeypatch) -> None:
    response = _FakeHttpResponse(302, {"Location": "https://oa.example.com/login"})
    _install_fake_urlopen(monkeypatch, response=response)

    report = network.http_health_check.invoke({"url": "https://oa.example.com"})

    assert "🟡 重定向到: https://oa.example.com/login" in report
    assert "重定向: https://oa.example.com/login" in report


def test_http_health_check_classifies_client_error(monkeypatch) -> None:
    _install_fake_urlopen(monkeypatch, response=_FakeHttpResponse(403, {}))

    report = network.http_health_check.invoke({"url": "https://oa.example.com"})

    assert "🔴 客户端错误" in report
    assert "HTTP 状态码: 403" in report


def test_http_health_check_advises_on_server_error(monkeypatch) -> None:
    _install_fake_urlopen(monkeypatch, response=_FakeHttpResponse(503, {}))

    report = network.http_health_check.invoke({"url": "https://oa.example.com"})

    assert "🔴 服务端错误" in report
    assert "5xx 错误排查建议" in report
    assert "检查反向代理（Nginx/HAProxy）状态" in report


def test_http_health_check_flags_slow_response(monkeypatch) -> None:
    _install_fake_urlopen(monkeypatch, response=_FakeHttpResponse(200, {}))
    monkeypatch.setattr(network.time, "time", Mock(side_effect=[1000.0, 1002.5]))

    report = network.http_health_check.invoke({"url": "https://oa.example.com"})

    assert "响应时间: 2500ms" in report
    assert "响应时间超过 2 秒" in report


def test_http_health_check_rejects_blank_url(monkeypatch) -> None:
    opener = _install_fake_urlopen(monkeypatch, response=_FakeHttpResponse(200, {}))

    assert (
        network.http_health_check.invoke({"url": "   "})
        == "[错误] 请输入完整的 URL（含 http:// 或 https://）"
    )
    opener.assert_not_called()


def test_http_health_check_reports_http_error(monkeypatch) -> None:
    error = urllib.error.HTTPError("https://oa.example.com", 502, "Bad Gateway", Message(), None)
    _install_fake_urlopen(monkeypatch, error=error)

    report = network.http_health_check.invoke({"url": "https://oa.example.com"})

    assert "🔴 HTTP 502 — Bad Gateway" in report


def test_http_health_check_advises_when_connection_fails(monkeypatch) -> None:
    _install_fake_urlopen(monkeypatch, error=urllib.error.URLError("连接被拒绝"))

    report = network.http_health_check.invoke({"url": "https://oa.example.com"})

    assert "🔴 连接失败 — 连接被拒绝" in report
    assert "确认 URL 是否正确" in report


def test_http_health_check_reports_ssl_and_unexpected_errors(monkeypatch) -> None:
    _install_fake_urlopen(monkeypatch, error=ssl.SSLError("certificate verify failed"))
    assert "SSL 握手失败" in network.http_health_check.invoke({"url": "https://oa.example.com"})

    _install_fake_urlopen(monkeypatch, error=ValueError("响应格式异常"))
    assert "健康检查异常: 响应格式异常" in network.http_health_check.invoke({"url": "https://oa.example.com"})


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


def test_network_diag_agent_uses_llm_when_configured(monkeypatch) -> None:
    fake_agent = SimpleNamespace(
        invoke=Mock(return_value={"messages": [_FakeLlmMessage("建议先检查网关与防火墙策略")]})
    )
    captured = _install_fake_llm_modules(monkeypatch, lambda: fake_agent)

    agent = network.NetworkDiagAgent(llm_api_key="sk-oa-test", llm_model="deepseek-chat")

    assert agent.diagnose("OA 服务访问缓慢") == "建议先检查网关与防火墙策略"
    assert captured["agent_kwargs"]["tools"] == network.NETWORK_DIAG_TOOLS
    assert captured["llm_kwargs"]["model"] == "deepseek-chat"


def test_network_diag_agent_falls_back_when_llm_init_fails(monkeypatch) -> None:
    def boom():
        raise RuntimeError("langchain 初始化失败")

    _install_fake_llm_modules(monkeypatch, boom)

    agent = network.NetworkDiagAgent(llm_api_key="sk-oa-test")

    assert agent._agent is None
    report = agent.diagnose("OA 服务访问缓慢")
    assert "[离线模式]" in report
    assert "host:port" in report


@pytest.mark.parametrize("api_key", ["", "ollama", "your-api-key-here"])
def test_network_diag_agent_skips_llm_for_placeholder_keys(api_key, monkeypatch) -> None:
    captured = _install_fake_llm_modules(monkeypatch, lambda: SimpleNamespace(invoke=Mock()))

    agent = network.NetworkDiagAgent(llm_api_key=api_key)

    assert agent._agent is None
    assert "agent_kwargs" not in captured
    assert "[离线模式]" in agent.diagnose("80 端口不通")


def test_network_diag_agent_falls_back_when_llm_returns_no_messages(monkeypatch) -> None:
    fake_agent = SimpleNamespace(invoke=Mock(return_value={"messages": []}))
    _install_fake_llm_modules(monkeypatch, lambda: fake_agent)

    agent = network.NetworkDiagAgent(llm_api_key="sk-oa-test")

    assert "[离线模式]" in agent.diagnose("路由异常")


def test_network_diag_agent_falls_back_when_llm_call_fails(monkeypatch) -> None:
    fake_agent = SimpleNamespace(invoke=Mock(side_effect=RuntimeError("模型服务不可用")))
    _install_fake_llm_modules(monkeypatch, lambda: fake_agent)

    agent = network.NetworkDiagAgent(llm_api_key="sk-oa-test")

    assert "[离线模式]" in agent.diagnose("DNS 解析异常")
