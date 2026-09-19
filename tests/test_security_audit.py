"""安全基线审计离线单测：subprocess 与文件系统边界全部打桩，不执行真实审计命令。

覆盖 _local_cmd 的取值与失败分支，五类审计工具（SSH/登录/防火墙/端口/cron）的
解析与判定分支，以及 SecurityAuditor 的 LLM 增强与离线降级路径。
"""

import io
import os
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agents import security_audit as security


class _FailingTool:
    """run_audit 降级时用于触发 "[tool.name] 执行失败" 分支。"""

    name = "audit_cron_jobs"

    @staticmethod
    def invoke(_payload):
        raise RuntimeError("命令超时")


def _fake_os(isdir, isfile, listdir):
    """构造只暴露审计代码所需接口的 os 替身，隔离真实系统路径。"""
    return SimpleNamespace(
        path=SimpleNamespace(isdir=isdir, isfile=isfile, join=os.path.join),
        listdir=listdir,
    )


# ---------- _local_cmd ----------

def test_local_cmd_list_argument_disables_shell(monkeypatch) -> None:
    runner = Mock(return_value=SimpleNamespace(stdout="root   pts/0  203.0.113.9\n", stderr="", returncode=0))
    monkeypatch.setattr(subprocess, "run", runner)
    monkeypatch.setattr(security, "IS_WINDOWS", False)

    assert security._local_cmd(["lastb", "-n", "20"]) == "root   pts/0  203.0.113.9"
    assert runner.call_args.kwargs["shell"] is False
    assert runner.call_args.kwargs["encoding"] == "utf-8"
    assert runner.call_args.kwargs["timeout"] == 10


def test_local_cmd_string_uses_shell_and_stderr_fallback(monkeypatch) -> None:
    runner = Mock(return_value=SimpleNamespace(stdout="", stderr="Status: active\n", returncode=0))
    monkeypatch.setattr(subprocess, "run", runner)
    monkeypatch.setattr(security, "IS_WINDOWS", True)

    assert security._local_cmd("ufw status verbose 2>/dev/null", timeout=5) == "Status: active"
    assert runner.call_args.kwargs["shell"] is True
    assert runner.call_args.kwargs["encoding"] == "gbk"
    assert runner.call_args.kwargs["timeout"] == 5


def test_local_cmd_returns_empty_on_timeout_and_message_on_failure(monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=subprocess.TimeoutExpired("ss", 10)))
    assert security._local_cmd("ss -tlnp") == ""

    monkeypatch.setattr(subprocess, "run", Mock(side_effect=OSError("permission denied")))
    assert security._local_cmd("iptables -L") == "执行失败: permission denied"


# ---------- audit_ssh_config ----------

def test_ssh_audit_explains_windows_scope(monkeypatch) -> None:
    monkeypatch.setattr(security, "IS_WINDOWS", True)

    report = security.audit_ssh_config.invoke({})

    assert "Windows 系统 — SSH 审计仅适用于 Linux" in report
    assert "config.yaml" in report


def test_ssh_audit_handles_missing_and_unreadable_config(monkeypatch) -> None:
    monkeypatch.setattr(security, "IS_WINDOWS", False)
    monkeypatch.setattr(security, "os", SimpleNamespace(path=SimpleNamespace(exists=lambda _path: False)))

    assert "❌ 未找到 SSHD 配置文件: /etc/ssh/sshd_config" in security.audit_ssh_config.invoke({})

    monkeypatch.setattr(security, "os", SimpleNamespace(path=SimpleNamespace(exists=lambda _path: True)))
    monkeypatch.setattr(security, "open", Mock(side_effect=PermissionError), raising=False)

    report = security.audit_ssh_config.invoke({})

    assert "❌ 无权限读取 /etc/ssh/sshd_config（需要 root 权限）" in report


def test_ssh_audit_flags_insecure_options(monkeypatch) -> None:
    config = "\n".join([
        "# 由 ansible 统一管理",
        "PermitRootLogin yes",
        "PasswordAuthentication yes",
        "PermitEmptyPasswords no",
        "Port 22",
        "MaxAuthTries 5",
        "Protocol 2",
        "X11Forwarding yes",
    ])
    monkeypatch.setattr(security, "IS_WINDOWS", False)
    monkeypatch.setattr(security, "os", SimpleNamespace(path=SimpleNamespace(exists=lambda _path: True)))
    monkeypatch.setattr(security, "open", Mock(return_value=io.StringIO(config)), raising=False)
    monkeypatch.setattr(security, "_local_cmd", Mock(return_value="active"))

    report = security.audit_ssh_config.invoke({})

    assert "⚠️ 发现以下安全风险:" in report
    assert "  🔴 PermitRootLogin: yes" in report
    assert "  🔴 PasswordAuthentication: yes" in report
    assert "  🔴 X11Forwarding: yes" in report
    assert "  🟡 Port: 22" in report
    assert "  🟡 MaxAuthTries: 5" in report
    assert "  ✅ PermitEmptyPasswords: no" in report
    assert "  ✅ Protocol: 2" in report
    assert "📋 SSH 服务状态: active" in report
    assert "systemctl restart sshd" in report


def test_ssh_audit_passes_safe_baseline(monkeypatch) -> None:
    config = "\n".join([
        "PermitRootLogin prohibit-password",
        "PasswordAuthentication no",
        "PermitEmptyPasswords no",
        "Port 22022",
        "MaxAuthTries 3",
        "Protocol 2",
        "X11Forwarding no",
    ])
    monkeypatch.setattr(security, "IS_WINDOWS", False)
    monkeypatch.setattr(security, "os", SimpleNamespace(path=SimpleNamespace(exists=lambda _path: True)))
    monkeypatch.setattr(security, "open", Mock(return_value=io.StringIO(config)), raising=False)
    monkeypatch.setattr(security, "_local_cmd", Mock(return_value=""))

    report = security.audit_ssh_config.invoke({})

    assert "✅ SSH 配置符合安全基线" in report
    assert "修改上述配置后执行" not in report
    assert "SSH 服务状态" not in report


def test_ssh_audit_warns_for_missing_directives(monkeypatch) -> None:
    monkeypatch.setattr(security, "IS_WINDOWS", False)
    monkeypatch.setattr(security, "os", SimpleNamespace(path=SimpleNamespace(exists=lambda _path: True)))
    monkeypatch.setattr(security, "open", Mock(return_value=io.StringIO("PermitRootLogin no")), raising=False)
    monkeypatch.setattr(security, "_local_cmd", Mock(return_value=""))

    report = security.audit_ssh_config.invoke({})

    assert "  ⚠️ PasswordAuthentication: 未配置（使用默认值，请检查）" in report
    assert "  ✅ PermitRootLogin: no" in report


# ---------- check_failed_logins ----------

@pytest.mark.parametrize("bad_limit", [None, "abc"])
def test_failed_logins_falls_back_to_default_limit(bad_limit, monkeypatch) -> None:
    monkeypatch.setattr(security, "IS_WINDOWS", False)
    monkeypatch.setattr(security.config, "get", Mock(return_value=bad_limit))
    runner = Mock(return_value="")
    monkeypatch.setattr(security, "_local_cmd", runner)

    security.check_failed_logins.invoke({})

    assert runner.call_args_list[0].args[0] == ["lastb", "-n", "20"]


def test_failed_logins_windows_event_log_summary(monkeypatch) -> None:
    long_line = "记录: " + "X" * 200 + "尾部标记"
    output = "\n".join(["登录失败: 用户 oa-admin", long_line] + [f"事件 {index}" for index in range(34)])
    runner = Mock(return_value=output)
    monkeypatch.setattr(security, "IS_WINDOWS", True)
    monkeypatch.setattr(security, "_local_cmd", runner)

    report = security.check_failed_logins.invoke({})

    assert "📋 Windows 登录失败记录 (Event ID 4625):" in report
    assert "登录失败: 用户 oa-admin" in report
    assert "尾部标记" not in report
    assert "... 完整记录请查看 Windows 事件查看器" in report
    assert runner.call_args.args[0].startswith("wevtutil qe Security")
    assert runner.call_args.kwargs["timeout"] == 15


def test_failed_logins_windows_without_readable_log(monkeypatch) -> None:
    monkeypatch.setattr(security, "IS_WINDOWS", True)
    monkeypatch.setattr(security, "_local_cmd", Mock(return_value=""))

    report = security.check_failed_logins.invoke({})

    assert "✅ 无最近失败登录记录（或安全日志不可读取）" in report


def test_failed_logins_windows_short_summary_without_hint(monkeypatch) -> None:
    monkeypatch.setattr(security, "IS_WINDOWS", True)
    monkeypatch.setattr(security, "_local_cmd", Mock(return_value="登录失败\n用户: oa-admin"))

    report = security.check_failed_logins.invoke({})

    assert "用户: oa-admin" in report
    assert "完整记录请查看" not in report


def test_failed_logins_linux_reports_repeated_source_ip(monkeypatch) -> None:
    lastb = "root ssh:notty 203.0.113.9 Mon Sep 15 01:00\ninvalid"
    auth_log = "\n".join(
        ["Failed password for root from 198.51.100.7 port 22022 ssh2"] * 3
        + ["pam_unix(sshd:auth): authentication failure; logname= uid=0"]
        + ["Failed password for admin from 203.0.113.9 port 22 ssh2"]
    )
    runner = Mock(side_effect=[lastb, auth_log])
    monkeypatch.setattr(security, "IS_WINDOWS", False)
    monkeypatch.setattr(security.config, "get", Mock(return_value=5))
    monkeypatch.setattr(security, "_local_cmd", runner)

    report = security.check_failed_logins.invoke({})

    assert "📋 最近 2 次失败登录 (lastb):" in report
    assert "用户: root | 来源: 203.0.113.9" in report
    assert "⚠️ 可疑 IP (≥3次失败):" in report
    assert "    🔴 198.51.100.7: 3 次失败登录" in report
    assert "无频繁失败登录的可疑IP" not in report
    assert "fail2ban" in report
    assert runner.call_args_list[1].args[0].startswith("grep 'Failed password' /var/log/auth.log")


def test_failed_logins_linux_without_any_records(monkeypatch) -> None:
    runner = Mock(side_effect=["", "Failed password for admin from 203.0.113.9 port 22 ssh2"])
    monkeypatch.setattr(security, "IS_WINDOWS", False)
    monkeypatch.setattr(security, "_local_cmd", runner)

    report = security.check_failed_logins.invoke({})

    assert "✅ lastb 无记录（或需要 root 权限）" in report
    assert "  无频繁失败登录的可疑IP" in report
    assert "用户:" not in report


# ---------- audit_firewall_rules ----------

def test_firewall_windows_shows_inbound_summary(monkeypatch) -> None:
    output = "\n".join([
        "规则名称:                            远程桌面",
        "已启用:                              是",
        "方向:                                入站",
        "操作:                                允许",
        "协议:                                TCP",
        "本地端口:                            3389",
        "",
    ])
    monkeypatch.setattr(security, "IS_WINDOWS", True)
    monkeypatch.setattr(security, "_local_cmd", Mock(return_value=output))

    report = security.audit_firewall_rules.invoke({})

    assert "📋 Windows 防火墙入站规则摘要:" in report
    assert "  规则名称:                            远程桌面" in report
    assert "  本地端口:                            3389" in report
    assert "(规则列表可能较长，仅显示摘要)" in report


def test_firewall_windows_without_admin_rights(monkeypatch) -> None:
    monkeypatch.setattr(security, "IS_WINDOWS", True)
    monkeypatch.setattr(security, "_local_cmd", Mock(return_value=""))

    report = security.audit_firewall_rules.invoke({})

    assert "⚠️ 无法读取防火墙规则，请以管理员身份运行" in report


def test_firewall_linux_summarizes_iptables_ufw_and_firewalld(monkeypatch) -> None:
    rules = "\n".join(f"   {index}   ACCEPT  tcp  --  0.0.0.0/0  0.0.0.0/0  tcp dpt:{8000 + index}"
                      for index in range(1, 21))
    iptables_input = "Chain INPUT (policy DROP)\ntarget     prot opt source     destination\n" + rules
    runner = Mock(side_effect=[
        iptables_input,
        "Chain FORWARD (policy DROP)\ntarget     prot opt source     destination",
        "Chain OUTPUT (policy ACCEPT)\ntarget     prot opt source     destination",
        "Status: active\n\nTo        Action    From\n--        ------    ----\n22/tcp    ALLOW     Anywhere",
        "public (active)\n  ports: 22/tcp 443/tcp",
    ])
    monkeypatch.setattr(security, "IS_WINDOWS", False)
    monkeypatch.setattr(security, "_local_cmd", runner)

    report = security.audit_firewall_rules.invoke({})

    assert "📋 iptables INPUT 链:" in report
    assert "  ... 共 20 条规则" in report
    assert report.count("tcp dpt:8015") == 1
    assert "tcp dpt:8016" not in report
    assert "(empty chain - policy: DROP)" in report
    assert "(empty chain - policy: ACCEPT)" in report
    assert "📋 UFW 状态:" in report
    assert "22/tcp    ALLOW     Anywhere" in report
    assert "📋 FirewallD 配置:" in report
    assert "ports: 22/tcp 443/tcp" in report
    assert runner.call_args_list[0].args[0] == ["iptables", "-L", "INPUT", "-n", "--line-numbers"]


def test_firewall_windows_without_truncation_hint(monkeypatch) -> None:
    rules = [f"规则名称: OA-端口{index}" for index in range(17)]
    monkeypatch.setattr(security, "IS_WINDOWS", True)
    monkeypatch.setattr(security, "_local_cmd", Mock(return_value="\n".join(rules)))

    report = security.audit_firewall_rules.invoke({})

    assert "  规则名称: OA-端口16" in report
    assert "仅显示摘要" not in report


def test_firewall_linux_skips_missing_iptables_and_short_chains(monkeypatch) -> None:
    forward = "\n".join([
        "Chain FORWARD (policy DROP)",
        "target     prot opt source     destination",
        "1    ACCEPT  tcp  --  0.0.0.0/0  0.0.0.0/0  tcp dpt:8000",
        "2    DROP    tcp  --  0.0.0.0/0  0.0.0.0/0  tcp dpt:8001",
    ])
    output_chain = "Chain OUTPUT\ntarget     prot opt source     destination"
    runner = Mock(side_effect=["", forward, output_chain, "Status: inactive", ""])
    monkeypatch.setattr(security, "IS_WINDOWS", False)
    monkeypatch.setattr(security, "_local_cmd", runner)

    report = security.audit_firewall_rules.invoke({})

    assert "iptables INPUT" not in report
    assert "  tcp dpt:8001" in report
    assert "... 共 " not in report
    assert report.count("(empty chain - policy: unknown)") == 1
    assert "UFW 状态" not in report
    assert "FirewallD 配置" not in report


def test_firewall_linux_skips_ufw_when_inactive(monkeypatch) -> None:
    empty_chain = "Chain INPUT\ntarget     prot opt source     destination"
    runner = Mock(side_effect=[empty_chain, empty_chain, empty_chain, "Status: inactive", ""])
    monkeypatch.setattr(security, "IS_WINDOWS", False)
    monkeypatch.setattr(security, "_local_cmd", runner)

    report = security.audit_firewall_rules.invoke({})

    assert report.count("(empty chain - policy: unknown)") == 3
    assert "UFW 状态" not in report
    assert "FirewallD 配置" not in report


# ---------- check_listening_ports ----------

def test_listening_ports_windows_flags_exposed_services(monkeypatch) -> None:
    output = "\n".join([
        "  TCP    0.0.0.0:8080           0.0.0.0:0              LISTENING       4321",
        "",
        "  TCP    127.0.0.1:5432         0.0.0.0:0              LISTENING       5000",
        "  TCP    0.0.0.0:3306           0.0.0.0:0              LISTENING       6000",
        "  TCP    0.0.0.0:22             0.0.0.0:0              LISTENING       700",
    ])
    monkeypatch.setattr(security, "IS_WINDOWS", True)
    monkeypatch.setattr(security, "_local_cmd", Mock(return_value=output))

    report = security.check_listening_ports.invoke({})

    assert "🌐 公网可访问端口（监听 0.0.0.0）:" in report
    assert "  ⚠️ 端口 3306 (MySQL)" in report
    assert "      🔴 危险: 数据库端口暴露在公网！请配置防火墙限制来源IP" in report
    assert "      🟡 注意: SSH 暴露在公网，建议限制来源IP或改用非标准端口" in report
    assert "    端口 8080 (HTTP-Alt/应用)" in report
    assert "🔒 本地监听端口（仅 127.0.0.1）:" in report
    assert "  ✅ 端口 5432 (PostgreSQL)" in report


def test_listening_ports_linux_public_bindings(monkeypatch) -> None:
    output = "LISTEN 0.0.0.0:22 0.0.0.0:*\n*:6379 *:*"
    monkeypatch.setattr(security, "IS_WINDOWS", False)
    monkeypatch.setattr(security, "_local_cmd", Mock(return_value=output))

    report = security.check_listening_ports.invoke({})

    assert "  ⚠️ 端口 22 (SSH)" in report
    assert "  ⚠️ 端口 6379 (Redis)" in report
    assert "      🔴 危险: 数据库端口暴露在公网！请配置防火墙限制来源IP" in report
    assert "🔒 本地监听端口" not in report


def test_listening_ports_linux_without_entries(monkeypatch) -> None:
    header = "State  Recv-Q Send-Q Local Address:Port  Peer Address:Port"
    monkeypatch.setattr(security, "IS_WINDOWS", False)
    monkeypatch.setattr(security, "_local_cmd", Mock(return_value=header))

    report = security.check_listening_ports.invoke({})

    assert "  无监听端口（异常）" in report
    assert "🔒 本地监听端口" not in report
    assert "🌐 公网可访问端口" not in report


def test_listening_ports_without_output(monkeypatch) -> None:
    monkeypatch.setattr(security, "IS_WINDOWS", False)
    monkeypatch.setattr(security, "_local_cmd", Mock(return_value=""))

    report = security.check_listening_ports.invoke({})

    assert "⚠️ 无法获取端口监听信息" in report
    assert "安全建议" not in report


def test_listening_ports_ss_format_masks_exposed_database(monkeypatch) -> None:
    """已知缺陷（随交付汇报）：ss -tlnp 的地址列不是行首第二个字段，地址正则失配后
    listen_addr 退化为 "?"，0.0.0.0:3306/6379 被错误归入「仅本地监听」且不触发危险告警。"""
    output = "\n".join([
        "State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process",
        'LISTEN 0      128    0.0.0.0:22      0.0.0.0:*     users:(("sshd",pid=1234,fd=3))',
        'LISTEN 0      4096   0.0.0.0:3306    0.0.0.0:*     users:(("mysqld",pid=1,fd=20))',
        'LISTEN 0      511    127.0.0.1:6379  0.0.0.0:*     users:(("redis-server",pid=2,fd=6))',
    ])
    monkeypatch.setattr(security, "IS_WINDOWS", False)
    monkeypatch.setattr(security, "_local_cmd", Mock(return_value=output))

    report = security.check_listening_ports.invoke({})

    assert "🔒 本地监听端口（仅 127.0.0.1）:" in report
    assert "🌐 公网可访问端口" not in report
    assert "🔴 危险" not in report


# ---------- audit_cron_jobs ----------

def test_cron_jobs_windows_lists_task_names(monkeypatch) -> None:
    output = "\n".join(f"任务名:    OA-备份任务{index}" for index in range(1, 33))
    monkeypatch.setattr(security, "IS_WINDOWS", True)
    monkeypatch.setattr(security, "_local_cmd", Mock(return_value=output))

    report = security.audit_cron_jobs.invoke({})

    assert "📋 Windows 计划任务 (摘要):" in report
    assert "  任务名:    OA-备份任务1" in report
    assert "... 共 32 个计划任务" in report
    assert "OA-备份任务31" not in report


def test_cron_jobs_windows_without_readable_tasks(monkeypatch) -> None:
    monkeypatch.setattr(security, "IS_WINDOWS", True)
    monkeypatch.setattr(security, "_local_cmd", Mock(return_value=""))

    report = security.audit_cron_jobs.invoke({})

    assert "⚠️ 无法读取计划任务" in report


def test_cron_jobs_windows_lists_limited_task_names(monkeypatch) -> None:
    output = "\n".join(f"任务名:    OA-巡检任务{index}" for index in range(1, 4))
    monkeypatch.setattr(security, "IS_WINDOWS", True)
    monkeypatch.setattr(security, "_local_cmd", Mock(return_value=output))

    report = security.audit_cron_jobs.invoke({})

    assert "  任务名:    OA-巡检任务3" in report
    assert "共 " not in report


def test_cron_jobs_linux_reads_system_files(monkeypatch) -> None:
    crontab = "# 每日 OA 备份\n0 2 * * * /opt/oa/scripts/backup.sh\n\n15 3 * * * /opt/oa/scripts/report.sh"
    cron_d = "*/5 * * * * root /opt/oa/scripts/health_check.sh"
    rotate = "\n".join(f"0 {hour} * * * root /opt/oa/scripts/rotate_logs.sh --keep={hour}" for hour in range(1, 13))

    def fake_open(path, *_args, **_kwargs):
        contents = {
            "/etc/cron.d/oa-backup": cron_d,
            "/etc/cron.d/oa-rotate": rotate,
            "/etc/cron.d/oa-empty": "   \n",
            "/etc/crontab": crontab,
        }
        if path in contents:
            return io.StringIO(contents[path])
        raise PermissionError(path)

    monkeypatch.setattr(security, "IS_WINDOWS", False)
    monkeypatch.setattr(
        security, "os",
        _fake_os(
            lambda path: path == "/etc/cron.d/",
            lambda path: path.startswith("/etc/cron.d/") or path == "/etc/crontab",
            lambda _path: ["oa-backup", "oa-rotate", "oa-empty"],
        ),
    )
    monkeypatch.setattr(security, "open", Mock(side_effect=fake_open), raising=False)
    monkeypatch.setattr(
        security, "_local_cmd",
        Mock(return_value="30 1 * * * /opt/oa/scripts/vacuum.sh\n# 临时任务：手工触发时用"),
    )

    report = security.audit_cron_jobs.invoke({})

    assert "📋 /etc/cron.d/oa-backup:" in report
    assert "  */5 * * * * root /opt/oa/scripts/health_check.sh" in report
    assert "📋 /etc/cron.d/oa-rotate:" in report
    assert "  ... 共 12 条定时任务" in report
    assert "oa-empty" not in report
    assert "📋 /etc/crontab:" in report
    assert "  0 2 * * * /opt/oa/scripts/backup.sh" in report
    assert "# 每日 OA 备份" not in report
    assert "📋 当前用户 crontab:" in report
    assert "  30 1 * * * /opt/oa/scripts/vacuum.sh" in report
    assert "# 临时任务" not in report


def test_cron_jobs_linux_reports_permission_problems(monkeypatch) -> None:
    monkeypatch.setattr(security, "IS_WINDOWS", False)
    monkeypatch.setattr(
        security, "os",
        _fake_os(lambda _path: True, lambda _path: False, Mock(side_effect=PermissionError("/etc/cron.d/"))),
    )
    monkeypatch.setattr(security, "_local_cmd", Mock(return_value="no crontab for oa-ops"))

    report = security.audit_cron_jobs.invoke({})

    assert "  ⚠️ 无权限访问 /etc/crontab" in report
    assert "当前用户 crontab" not in report

    monkeypatch.setattr(
        security, "os",
        _fake_os(lambda _path: False, lambda path: path == "/etc/crontab", lambda _path: []),
    )
    monkeypatch.setattr(security, "open", Mock(side_effect=PermissionError("/etc/crontab")), raising=False)

    report = security.audit_cron_jobs.invoke({})

    assert "  ⚠️ 无权限读取 /etc/crontab" in report

    monkeypatch.setattr(
        security, "os",
        _fake_os(
            lambda path: path == "/etc/cron.d/",
            lambda path: path == "/etc/cron.d/oa-backup",
            lambda _path: ["oa-backup", "oa-subdir"],
        ),
    )
    monkeypatch.setattr(security, "open", Mock(side_effect=PermissionError("/etc/cron.d/oa-backup")), raising=False)

    report = security.audit_cron_jobs.invoke({})

    assert "  ⚠️ 无权限读取 /etc/cron.d/oa-backup" in report
    assert "oa-subdir" not in report


def test_cron_jobs_linux_without_entries_skips_empty_notice(monkeypatch) -> None:
    """已知缺陷（随交付汇报）：头部固定 5 行使 `len(lines) <= 3` 恒为假，
    「✅ 未发现 crontab 任务」分支不可达。"""
    monkeypatch.setattr(security, "IS_WINDOWS", False)
    monkeypatch.setattr(security, "os", _fake_os(lambda _path: False, lambda _path: False, lambda _path: []))
    monkeypatch.setattr(security, "_local_cmd", Mock(return_value=""))

    report = security.audit_cron_jobs.invoke({})

    assert "Crontab 定时任务审计" in report
    assert "未发现 crontab 任务" not in report


# ---------- SecurityAuditor ----------

@pytest.mark.parametrize("api_key", ["", "ollama", "your-api-key-here"])
def test_auditor_skips_llm_without_real_key(api_key) -> None:
    assert security.SecurityAuditor(llm_api_key=api_key)._agent is None


def test_auditor_initializes_and_degrades_llm_agent(monkeypatch) -> None:
    create_agent = Mock(name="create_agent")
    monkeypatch.setattr("langchain_openai.ChatOpenAI", Mock(return_value=Mock()), raising=False)
    monkeypatch.setattr("langchain.agents.create_agent", create_agent, raising=False)

    assert security.SecurityAuditor(llm_api_key="sk-oa-test")._agent is create_agent.return_value

    monkeypatch.setattr("langchain_openai.ChatOpenAI", Mock(side_effect=RuntimeError("无网络")), raising=False)

    assert security.SecurityAuditor(llm_api_key="sk-oa-test")._agent is None


def test_auditor_single_audit_rejects_unknown_type() -> None:
    report = security.SecurityAuditor().single_audit("disk")

    assert "不支持的审计类型: disk" in report
    assert "['ssh', 'login', 'firewall', 'ports', 'cron']" in report


def test_auditor_single_audit_delegates_to_tool(monkeypatch) -> None:
    monkeypatch.setattr(security, "IS_WINDOWS", False)
    monkeypatch.setattr(security, "_local_cmd", Mock(return_value=""))

    assert "⚠️ 无法获取端口监听信息" in security.SecurityAuditor().single_audit("ports")


def test_auditor_offline_report_runs_all_tools(monkeypatch) -> None:
    ok_tool = Mock()
    ok_tool.invoke.return_value = "插件巡检通过"
    monkeypatch.setattr(security, "SECURITY_AUDIT_TOOLS", [ok_tool, _FailingTool()])

    report = security.SecurityAuditor().run_audit()

    assert "安全基线审计报告（离线模式）" in report
    assert "插件巡检通过" in report
    assert "[audit_cron_jobs] 执行失败: 命令超时" in report
    assert "提示: 配置 API Key 可获得 AI 风险评估和改进建议" in report


def test_auditor_run_audit_uses_llm_answer(monkeypatch) -> None:
    auditor = security.SecurityAuditor()
    auditor._agent = Mock()
    auditor._agent.invoke.return_value = {"messages": [SimpleNamespace(content="AI 风险评估：SSH 应限制来源 IP")]}

    assert auditor.run_audit() == "AI 风险评估：SSH 应限制来源 IP"
    prompt = auditor._agent.invoke.call_args.args[0]["messages"][0]["content"]
    assert "完整的安全基线审计" in prompt


@pytest.mark.parametrize("empty_messages", [False, True])
def test_auditor_run_audit_degrades_on_llm_problems(empty_messages, monkeypatch) -> None:
    auditor = security.SecurityAuditor()
    auditor._agent = Mock()
    if empty_messages:
        auditor._agent.invoke.return_value = {"messages": []}
    else:
        auditor._agent.invoke.side_effect = RuntimeError("429 rate limit")
    monkeypatch.setattr(security, "SECURITY_AUDIT_TOOLS", [])

    report = auditor.run_audit()

    assert "安全基线审计报告（离线模式）" in report
    assert "提示: 配置 API Key" in report
