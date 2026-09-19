"""Regression checks at command boundaries; no real security audit is executed."""

from unittest.mock import Mock

import pytest

from agents import security_audit as security


@pytest.mark.parametrize("limit,expected", [(0, "1"), (1000, "100"), (10, "10"), ("oops", "20")])
def test_failed_login_command_clamps_configuration(limit, expected, monkeypatch):
    monkeypatch.setattr(security, "IS_WINDOWS", False)
    monkeypatch.setattr(security.config, "get", Mock(return_value=limit))
    runner = Mock(return_value="")
    monkeypatch.setattr(security, "_local_cmd", runner)
    assert "无记录" in security.check_failed_logins.invoke({})
    assert runner.call_args_list[0].args[0] == ["lastb", "-n", expected]


def test_failed_logins_reports_repeated_source(monkeypatch):
    monkeypatch.setattr(security, "IS_WINDOWS", False)
    runner = Mock(side_effect=[
        "admin ssh:notty 192.0.2.5 Tue Sep 15 01:00",
        "Failed password from 192.0.2.5\n" * 4,
    ])
    monkeypatch.setattr(security, "_local_cmd", runner)
    report = security.check_failed_logins.invoke({})
    assert "192.0.2.5: 4 次失败登录" in report


def test_ssh_audit_explains_windows_limit(monkeypatch):
    monkeypatch.setattr(security, "IS_WINDOWS", True)
    assert "仅适用于 Linux" in security.audit_ssh_config.invoke({})


def test_firewall_linux_passes_chain_as_separate_argument(monkeypatch):
    monkeypatch.setattr(security, "IS_WINDOWS", False)
    runner = Mock(side_effect=[
        "Chain INPUT (policy DROP)\ntarget prot opt source destination",
        "Chain FORWARD (policy DROP)",
        "Chain OUTPUT (policy ACCEPT)",
        "Status: inactive",
        "",
    ])
    monkeypatch.setattr(security, "_local_cmd", runner)
    report = security.audit_firewall_rules.invoke({})
    assert "policy: DROP" in report
    for call, chain in zip(runner.call_args_list[:3], ["INPUT", "FORWARD", "OUTPUT"]):
        assert call.args[0] == ["iptables", "-L", chain, "-n", "--line-numbers"]
