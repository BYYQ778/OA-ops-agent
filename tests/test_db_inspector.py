"""数据库巡检工具离线单测：subprocess 边界全部打桩，不联网、不启动真实客户端。

用例按「函数 × 分支」组织，覆盖：
- _exec_sql / _exec_redis / _exec_sqlcmd / _exec_sqlplus 的命令构造与错误映射
- MySQL / Redis / SQL Server / Oracle 报告解析、阈值判断与容错分支
- DBCheckAgent 的 LLM 可用、降级、空消息与参数透传路径
"""

import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agents import db_inspector as db


def _proc(stdout: str = "", stderr: str = "", returncode: int = 0) -> SimpleNamespace:
    """subprocess.run 返回值替身（只保留模块读取的字段）。"""
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def _mysql_router(**overrides):
    """按 SQL 内容分派的 _exec_sql 桩；参数传 Exception 可模拟查询中断。"""
    values = {
        "version": "8.0.36",
        "connections": "12\t12\t151",
        "threshold": "10.000000",
        "slow_count": "0",
        "innodb": "1000\t2",
        "slave": "Seconds_Behind_Master: 0",
    }
    values.update(overrides)
    markers = (
        ("VERSION()", values["version"]),
        ("@@max_connections", values["connections"]),
        ("long_query_time", values["threshold"]),
        ("COUNT(*) FROM mysql.slow_log", values["slow_count"]),
        ("Innodb_buffer_pool_read_requests", values["innodb"]),
        ("SHOW SLAVE STATUS", values["slave"]),
    )

    def route(host, port, user, password, sql, database=""):
        for marker, value in markers:
            if marker in sql:
                if isinstance(value, Exception):
                    raise value
                return value
        raise AssertionError(f"未预期的 SQL: {sql}")

    return route


def _mssql_router(**overrides):
    """按 SQL 内容分派的 _exec_sqlcmd 桩，模拟 sqlcmd -s "|" 的输出。"""
    values = {
        "version": "Microsoft SQL Server 2019 (RTM-CU25) - 15.0.4360.2 (X64)",
        "connections": "12|15|2",
        "size": "OA_Main|2048.0 MB",
        "backup": "OA_Main|2026-05-01 03:00:00",
    }
    values.update(overrides)
    markers = (
        ("@@VERSION", values["version"]),
        ("dm_exec_connections", values["connections"]),
        ("sys.master_files", values["size"]),
        ("backupset", values["backup"]),
    )

    def route(host, port, user, password, sql, database="master"):
        for marker, value in markers:
            if marker in sql:
                if isinstance(value, Exception):
                    raise value
                return value
        raise AssertionError(f"未预期的 SQL: {sql}")

    return route


def _oracle_router(**overrides):
    """按 SQL 内容分派的 _exec_sqlplus 桩。"""
    values = {
        "version": (
            "VERSION:Oracle Database 19c Enterprise Edition Release 19.0.0.0.0 - Production\n"
            "INSTANCE:ORCL | STATUS:OPEN | HOST:oa-db01\n"
            "STARTUP:2026-01-02 09:30:00"
        ),
        "tablespace": "SYSTEM|1024MB|2048MB|50%|OK",
        "sessions": "37 total sessions\n5 active sessions\n1 blocking sessions",
        "archive": "LOG_MODE: ARCHIVELOG\nARCHIVE_DEST: LOG_ARCHIVE_DEST_1 | STATUS: VALID",
    }
    values.update(overrides)
    markers = (
        ("v$version", values["version"]),
        ("TABLESPACE_NAME", values["tablespace"]),
        ("v$session", values["sessions"]),
        ("v$archive_dest", values["archive"]),
    )

    def route(host, port, user, password, sql, service_name=""):
        for marker, value in markers:
            if marker in sql:
                if isinstance(value, Exception):
                    raise value
                return value
        raise AssertionError(f"未预期的 SQL: {sql}")

    return route


def _redis_info(**overrides) -> str:
    """构造 Redis INFO 输出；传 None 表示该字段整体缺失。"""
    fields = {
        "used_memory_human": "1.29M",
        "maxmemory_human": "0B",
        "mem_fragmentation_ratio": "1.05",
        "connected_clients": "12",
        "blocked_clients": "0",
        "keyspace_hits": "9900",
        "keyspace_misses": "100",
        "expired_keys": "5",
        "evicted_keys": "0",
        "rdb_last_save_time": "1735689600",
        "rdb_changes_since_last_save": "0",
        "aof_enabled": "1",
        "slowlog_len": "0",
    }
    fields.update(overrides)
    body = "\n".join(f"{key}:{value}" for key, value in fields.items() if value is not None)
    return f"# Server\n{body}\n# Keyspace\ndb0:keys=10,expires=2,avg_ttl=0\ndb1:keys=5,expires=0,avg_ttl=0\n"


# ---------- _exec_sql ----------

def test_exec_sql_builds_batch_command_with_database(monkeypatch) -> None:
    monkeypatch.setattr(db, "IS_WINDOWS", False)
    runner = Mock(return_value=_proc(stdout=" 8.0.36 \n"))
    monkeypatch.setattr(subprocess, "run", runner)

    assert db._exec_sql("db01", 3307, "ops", "s3cret", "SELECT 1", database="oa") == "8.0.36"
    assert runner.call_args.args[0] == "mysql -hdb01 -P3307 -uops -ps3cret -N -B oa -e SELECT 1"
    assert runner.call_args.kwargs["shell"] is True
    assert runner.call_args.kwargs["timeout"] == 15
    assert runner.call_args.kwargs["encoding"] == "utf-8"


def test_exec_sql_windows_uses_exe_and_gbk(monkeypatch) -> None:
    monkeypatch.setattr(db, "IS_WINDOWS", True)
    runner = Mock(return_value=_proc(stdout="5.7.44"))
    monkeypatch.setattr(subprocess, "run", runner)

    assert db._exec_sql("127.0.0.1", 3306, "root", "p", "SELECT VERSION()") == "5.7.44"
    assert runner.call_args.args[0].startswith("mysql.exe -h127.0.0.1 -P3306 -uroot -pp -N -B -e")
    assert runner.call_args.kwargs["encoding"] == "gbk"


def test_exec_sql_maps_known_client_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        subprocess, "run",
        Mock(return_value=_proc(stderr="ERROR 1045 (28000): Access denied for user 'ops'@'cli'", returncode=1)),
    )
    assert db._exec_sql("127.0.0.1", 3306, "ops", "bad", "SELECT 1") == "__ERROR__: 数据库认证失败，请检查用户名和密码"

    monkeypatch.setattr(
        subprocess, "run",
        Mock(return_value=_proc(
            stderr="ERROR 2003 (HY000): Can't connect to MySQL server on 'db01' (111)", returncode=1,
        )),
    )
    assert db._exec_sql("db01", 3306, "ops", "p", "SELECT 1") == (
        "__ERROR__: 无法连接到 db01:3306，请检查数据库服务状态和防火墙"
    )


def test_exec_sql_truncates_unknown_error_and_handles_timeout(monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run", Mock(return_value=_proc(stderr="E" * 300, returncode=1)))
    assert db._exec_sql("db01", 3306, "ops", "p", "SELECT 1") == f"__ERROR__: {'E' * 200}"

    monkeypatch.setattr(subprocess, "run", Mock(side_effect=subprocess.TimeoutExpired("mysql", 15)))
    assert db._exec_sql("db01", 3306, "ops", "p", "SELECT 1") == "__ERROR__: 查询超时"

    monkeypatch.setattr(subprocess, "run", Mock(side_effect=FileNotFoundError("mysql")))
    assert db._exec_sql("db01", 3306, "ops", "p", "SELECT 1") == "__ERROR__: mysql 客户端未安装，请先安装 MySQL Client"


# ---------- _exec_redis ----------

def test_exec_redis_builds_command_with_optional_password(monkeypatch) -> None:
    monkeypatch.setattr(db, "IS_WINDOWS", False)
    runner = Mock(return_value=_proc(stdout="PONG\n"))
    monkeypatch.setattr(subprocess, "run", runner)

    assert db._exec_redis("redis01", 6379, "s3cret", "INFO memory") == "PONG"
    assert runner.call_args.args[0] == "redis-cli -h redis01 -p 6379 -a s3cret INFO memory"
    assert runner.call_args.kwargs["timeout"] == 10

    assert db._exec_redis("redis01", 6379, "", "PING") == "PONG"
    assert runner.call_args.args[0] == "redis-cli -h redis01 -p 6379 PING"


def test_exec_redis_falls_back_to_stderr_and_maps_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        subprocess, "run",
        Mock(return_value=_proc(stdout="", stderr="NOAUTH Authentication required.")),
    )
    assert db._exec_redis("127.0.0.1", 6379, "", "PING") == "__ERROR__: Redis 认证失败，请检查密码"

    monkeypatch.setattr(
        subprocess, "run",
        Mock(return_value=_proc(stdout="", stderr="Could not connect to Redis at 127.0.0.1:6379: Connection refused")),
    )
    assert db._exec_redis("127.0.0.1", 6379, "", "PING") == "__ERROR__: 无法连接到 127.0.0.1:6379"

    monkeypatch.setattr(subprocess, "run", Mock(side_effect=subprocess.TimeoutExpired("redis-cli", 10)))
    assert db._exec_redis("127.0.0.1", 6379, "", "PING") == "__ERROR__: Redis 查询超时"

    monkeypatch.setattr(subprocess, "run", Mock(side_effect=FileNotFoundError("redis-cli")))
    assert db._exec_redis("127.0.0.1", 6379, "", "PING") == "__ERROR__: redis-cli 客户端未安装"


# ---------- check_mysql_status ----------

def test_mysql_status_requires_password_and_falls_back_to_env(monkeypatch) -> None:
    monkeypatch.delenv("OA_MYSQL_PASSWORD", raising=False)
    report = db.check_mysql_status.invoke({"config_text": "host=10.0.0.9 port=3306 user=ops"})
    assert "未提供数据库密码" in report
    assert "OA_MYSQL_PASSWORD" in report

    runner = Mock(return_value="__ERROR__: 查询超时")
    monkeypatch.setattr(db, "_exec_sql", runner)
    monkeypatch.setenv("OA_MYSQL_PASSWORD", "env-secret")
    report = db.check_mysql_status.invoke({"config_text": "host=10.0.0.9 port=3306 user=ops"})
    assert "❌ 连接失败: 查询超时" in report
    assert runner.call_args.args == ("10.0.0.9", 3306, "ops", "env-secret", "SELECT VERSION()")


def test_mysql_status_connection_failure_prints_checklist(monkeypatch) -> None:
    runner = Mock(return_value="__ERROR__: 无法连接到 10.0.0.9:3306，请检查数据库服务状态和防火墙")
    monkeypatch.setattr(db, "_exec_sql", runner)

    report = db.check_mysql_status.invoke({"config_text": "host=10.0.0.9 port=3306 user=ops password=x"})

    assert "❌ 连接失败: 无法连接到 10.0.0.9:3306" in report
    assert "netstat -tlnp | grep 3306" in report
    assert runner.call_count == 1


def test_mysql_status_full_healthy_report(monkeypatch) -> None:
    monkeypatch.setattr(db, "_exec_sql", Mock(side_effect=_mysql_router()))

    report = db.check_mysql_status.invoke({"config_text": "host=db01 port=3307 user=ops password=Secret#1"})

    assert "目标: db01:3307" in report
    assert "✅ 连接成功 — MySQL 版本: 8.0.36" in report
    assert "✅ 连接使用率: 12/151 (7.9%)" in report
    assert "✅ 近24小时慢查询: 0 条" in report
    assert "✅ InnoDB 缓冲池命中率: 99.8%" in report
    assert "✅ 主从复制延迟: 0秒" in report


def test_mysql_status_reports_slow_queries_above_zero(monkeypatch) -> None:
    monkeypatch.setattr(db, "_exec_sql", Mock(side_effect=_mysql_router(slow_count="3")))

    report = db.check_mysql_status.invoke({"config_text": "host=db01 user=ops password=x"})

    assert "🟡 近24小时慢查询: 3 条 (阈值: 10.000000秒)" in report


def test_mysql_status_warns_when_connections_near_limit(monkeypatch) -> None:
    monkeypatch.setattr(db, "_exec_sql", Mock(side_effect=_mysql_router(connections="145\t145\t151")))

    report = db.check_mysql_status.invoke({"config_text": "host=db01 user=ops password=x"})

    assert "⚠️ 连接使用率: 145/151 (96.0%) — 接近上限！" in report


@pytest.mark.parametrize(
    ("connections", "expected"),
    [
        ("many\tmany\tlots", "✅ 连接使用率: many/lots (0%)"),
        ("5\t5\t0", "✅ 连接使用率: 5/0 (0%)"),
        ("only-two", None),
    ],
)
def test_mysql_status_tolerates_malformed_connection_numbers(connections, expected, monkeypatch) -> None:
    monkeypatch.setattr(db, "_exec_sql", Mock(side_effect=_mysql_router(connections=connections)))

    report = db.check_mysql_status.invoke({"config_text": "host=db01 user=ops password=x"})

    if expected is None:
        assert "连接使用率" not in report
    else:
        assert expected in report


@pytest.mark.parametrize(
    ("slow_count", "expected"),
    [
        ("3", "🟡 近24小时慢查询: 3 条"),
        ("ERROR 1146 (42S02) at line 1: Table 'mysql.slow_log' doesn't exist", "慢查询日志未开启"),
        ("__ERROR__: 查询超时", "慢查询日志未开启"),
        ("", "慢查询日志未开启"),
    ],
)
def test_mysql_status_slow_log_variants(slow_count, expected, monkeypatch) -> None:
    monkeypatch.setattr(db, "_exec_sql", Mock(side_effect=_mysql_router(slow_count=slow_count)))

    report = db.check_mysql_status.invoke({"config_text": "host=db01 user=ops password=x"})

    assert expected in report


def test_mysql_status_swallows_slow_log_query_failure(monkeypatch) -> None:
    router = _mysql_router(slow_count=RuntimeError("MySQL server has gone away"))
    monkeypatch.setattr(db, "_exec_sql", Mock(side_effect=router))

    report = db.check_mysql_status.invoke({"config_text": "host=db01 user=ops password=x"})

    assert "InnoDB 缓冲池命中率" in report
    assert "近24小时慢查询" not in report


@pytest.mark.parametrize(
    ("innodb", "expected"),
    [
        ("1000\t2", "✅ InnoDB 缓冲池命中率: 99.8%"),
        ("1000\t40", "🟡 InnoDB 缓冲池命中率: 96.0% — 偏低，考虑增大 innodb_buffer_pool_size"),
        ("1000\t500", "🔴 InnoDB 缓冲池命中率: 50.0% — 严重偏低！"),
        ("0\t0", None),
        ("garbage", None),
    ],
)
def test_mysql_status_innodb_hit_rate_branches(innodb, expected, monkeypatch) -> None:
    monkeypatch.setattr(db, "_exec_sql", Mock(side_effect=_mysql_router(innodb=innodb)))

    report = db.check_mysql_status.invoke({"config_text": "host=db01 user=ops password=x"})

    if expected is None:
        assert "InnoDB 缓冲池命中率" not in report
    else:
        assert expected in report


@pytest.mark.parametrize(
    ("slave", "expected"),
    [
        ("Seconds_Behind_Master: NULL", "ℹ️ 主从复制: 未配置或SLAVE未运行"),
        ("Seconds_Behind_Master: 0", "✅ 主从复制延迟: 0秒"),
        ("Seconds_Behind_Master: 12", "🟡 主从复制延迟: 12秒"),
        ("Seconds_Behind_Master: 0\nSeconds_Behind_Master: 12", "✅ 主从复制延迟: 0秒"),
        ("Seconds_Behind_Master 状态未知", None),
        ("Slave_IO_Running: Yes", None),
    ],
)
def test_mysql_status_slave_delay_branches(slave, expected, monkeypatch) -> None:
    monkeypatch.setattr(db, "_exec_sql", Mock(side_effect=_mysql_router(slave=slave)))

    report = db.check_mysql_status.invoke({"config_text": "host=db01 user=ops password=x"})

    if expected is None:
        assert "主从复制" not in report
    else:
        assert expected in report


def test_mysql_status_parses_multiline_config_and_ignores_stray_tokens(monkeypatch) -> None:
    runner = Mock(return_value="__ERROR__: 查询超时")
    monkeypatch.setattr(db, "_exec_sql", runner)

    db.check_mysql_status.invoke({"config_text": "  host=db01\nport=3308\nuser=ops\npassword=p  verbose\n"})

    assert runner.call_args.args[:5] == ("db01", 3308, "ops", "p", "SELECT VERSION()")


@pytest.mark.parametrize(
    ("overrides", "absent"),
    [
        ({"connections": "__ERROR__: 查询超时"}, "连接使用率"),
        ({"threshold": "__ERROR__: 查询超时"}, "近24小时慢查询"),
        ({"innodb": "__ERROR__: 查询超时"}, "InnoDB 缓冲池命中率"),
        ({"innodb": RuntimeError("Lost connection to MySQL server")}, "InnoDB 缓冲池命中率"),
        ({"slave": RuntimeError("Lost connection to MySQL server")}, "主从复制"),
    ],
)
def test_mysql_status_skips_sections_when_queries_fail(overrides, absent, monkeypatch) -> None:
    monkeypatch.setattr(db, "_exec_sql", Mock(side_effect=_mysql_router(**overrides)))

    report = db.check_mysql_status.invoke({"config_text": "host=db01 user=ops password=x"})

    assert "✅ 连接成功 — MySQL 版本: 8.0.36" in report
    assert absent not in report


# ---------- show_mysql_slow_queries ----------

def test_show_slow_queries_requires_password_and_maps_errors(monkeypatch) -> None:
    monkeypatch.delenv("OA_MYSQL_PASSWORD", raising=False)
    assert db.show_mysql_slow_queries.invoke({"config_text": "host=db01 user=ops"}) == "[提示] 请提供数据库密码"

    monkeypatch.setattr(db, "_exec_sql", Mock(return_value="__ERROR__: 查询超时"))
    assert db.show_mysql_slow_queries.invoke(
        {"config_text": "host=db01 user=ops password=x"}
    ) == "查询失败: 查询超时"

    monkeypatch.setattr(db, "_exec_sql", Mock(return_value=""))
    assert db.show_mysql_slow_queries.invoke(
        {"config_text": "host=db01 user=ops password=x"}
    ) == "✅ 近24小时无慢查询记录"


def test_show_slow_queries_renders_rows_with_limit(monkeypatch) -> None:
    runner = Mock(return_value="2026-09-16 21:30:00\t0.42\t15\t120033\tSELECT * FROM oa_flow WHERE status='待审批'")
    monkeypatch.setattr(db, "_exec_sql", runner)

    report = db.show_mysql_slow_queries.invoke({"config_text": "host=db01 user=ops password=x limit=5"})

    assert "MySQL 慢查询记录 (最近 5 条)" in report
    assert "2026-09-16 21:30:00 | 0.42 | 15 | 120033 | SELECT * FROM oa_flow WHERE status='待审批'" in report
    assert "ORDER BY start_time DESC LIMIT 5" in runner.call_args.args[4]


# ---------- check_redis_status ----------

def test_redis_status_reports_ping_failure(monkeypatch) -> None:
    monkeypatch.setattr(db, "_exec_redis", Mock(return_value="__ERROR__: 无法连接到 10.0.0.8:6379"))
    report = db.check_redis_status.invoke({"config_text": "host=10.0.0.8 port=6379"})
    assert "❌ 连接失败" in report
    assert "systemctl status redis" in report

    monkeypatch.setattr(
        db, "_exec_redis", Mock(return_value="LOADING Redis is loading the dataset in memory")
    )
    assert "❌ 连接失败" in db.check_redis_status.invoke({"config_text": "host=10.0.0.8 port=6379"})


def test_redis_status_reports_info_failure(monkeypatch) -> None:
    monkeypatch.setattr(db, "_exec_redis", Mock(side_effect=["PONG", "__ERROR__: Redis 查询超时"]))

    report = db.check_redis_status.invoke({"config_text": "host=redis01 port=6379 password=s3cret"})

    assert "❌ 获取 INFO 失败: __ERROR__: Redis 查询超时" in report


def test_redis_status_full_healthy_report(monkeypatch) -> None:
    monkeypatch.setattr(
        db, "_exec_redis",
        Mock(side_effect=["PONG", _redis_info(mem_fragmentation_ratio="3.20", evicted_keys="2", slowlog_len="3")]),
    )

    report = db.check_redis_status.invoke({"config_text": "host=redis01 port=6379"})

    assert "📊 内存使用: 1.29M / 0B" in report
    assert "⚠️ 内存碎片率: 3.20 (> 2，建议执行 MEMORY PURGE)" in report
    assert "✅ 缓存命中率: 99.0%" in report
    assert "🔗 连接数: 12 客户端 (阻塞: 0)" in report
    assert "🗂️ 键总数: 15 个" in report
    assert "⚠️ 因内存不足驱逐的键: 2 个 (考虑增大 maxmemory)" in report
    assert "📅 已过期键: 5 个" in report
    assert "✅ AOF 持久化已开启" in report
    assert "🟡 Redis 慢日志: 3 条 (可用 SLOWLOG GET 查看)" in report
    expected_save = datetime.fromtimestamp(1735689600).strftime("%Y-%m-%d %H:%M:%S")
    assert f"💾 RDB 最后保存: {expected_save} (变更: 0)" in report


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"keyspace_hits": "0", "keyspace_misses": "0"}, "ℹ️ 无键操作记录"),
        ({"keyspace_hits": "85", "keyspace_misses": "15"}, "🟡 缓存命中率: 85.0% — 偏低"),
        ({"keyspace_hits": "50", "keyspace_misses": "50"}, "🔴 缓存命中率: 50.0% — 严重偏低"),
        ({"rdb_last_save_time": "0"}, "⚠️ RDB 持久化未开启或未保存"),
        ({"aof_enabled": "0"}, "ℹ️ AOF 持久化未开启 (仅 RDB)"),
        ({"evicted_keys": "0"}, "驱逐的键"),
        ({"mem_fragmentation_ratio": "1.05"}, "内存碎片率"),
        ({"mem_fragmentation_ratio": None}, "内存碎片率"),
    ],
)
def test_redis_status_threshold_variants(overrides, expected, monkeypatch) -> None:
    monkeypatch.setattr(db, "_exec_redis", Mock(side_effect=["PONG", _redis_info(**overrides)]))

    report = db.check_redis_status.invoke({"config_text": "host=redis01 port=6379"})

    if expected in ("驱逐的键", "内存碎片率"):
        assert expected not in report
    else:
        assert expected in report


# ---------- _exec_sqlcmd / check_mssql_status ----------

def test_exec_sqlcmd_builds_command_and_maps_login_error(monkeypatch) -> None:
    monkeypatch.setattr(db, "IS_WINDOWS", True)
    runner = Mock(return_value=_proc(stderr="Login failed for user 'sa'.", returncode=1))
    monkeypatch.setattr(subprocess, "run", runner)

    assert db._exec_sqlcmd("10.0.0.10", 1433, "sa", "bad", "SELECT 1") == "__ERROR__: 登录失败，请检查用户名和密码"
    assert runner.call_args.args[0] == (
        'sqlcmd.exe -S 10.0.0.10,1433 -U sa -P bad -d master -h -1 -W -s "|" -Q "SELECT 1"'
    )
    assert runner.call_args.kwargs["encoding"] == "gbk"


def test_exec_sqlcmd_error_paths(monkeypatch) -> None:
    monkeypatch.setattr(db, "IS_WINDOWS", False)
    monkeypatch.setattr(subprocess, "run", Mock(return_value=_proc(stdout=" 1 \n")))
    assert db._exec_sqlcmd("db-mssql01", 1433, "sa", "p", "SELECT 1", database="oa") == "1"

    monkeypatch.setattr(
        subprocess, "run",
        Mock(return_value=_proc(stderr="Cannot open server 'db-mssql01' requested by the login.", returncode=1)),
    )
    assert db._exec_sqlcmd("db-mssql01", 1433, "sa", "p", "SELECT 1") == "__ERROR__: 无法连接到 db-mssql01:1433"

    monkeypatch.setattr(subprocess, "run", Mock(return_value=_proc(stderr="Msg 4060: 无法打开数据库", returncode=1)))
    assert db._exec_sqlcmd("db-mssql01", 1433, "sa", "p", "SELECT 1") == "__ERROR__: Msg 4060: 无法打开数据库"

    monkeypatch.setattr(subprocess, "run", Mock(side_effect=subprocess.TimeoutExpired("sqlcmd", 15)))
    assert db._exec_sqlcmd("db-mssql01", 1433, "sa", "p", "SELECT 1") == "__ERROR__: 查询超时"

    monkeypatch.setattr(subprocess, "run", Mock(side_effect=FileNotFoundError("sqlcmd")))
    assert db._exec_sqlcmd("db-mssql01", 1433, "sa", "p", "SELECT 1") == (
        "__ERROR__: sqlcmd 未安装，请安装 SQL Server Command Line Tools"
    )


def test_mssql_status_requires_password_and_env(monkeypatch) -> None:
    monkeypatch.delenv("OA_MSSQL_PASSWORD", raising=False)
    report = db.check_mssql_status.invoke({"config_text": "host=10.0.0.10 port=1433 user=sa"})
    assert "未提供数据库密码" in report
    assert "port=1433" in report

    runner = Mock(return_value="__ERROR__: 登录失败，请检查用户名和密码")
    monkeypatch.setattr(db, "_exec_sqlcmd", runner)
    monkeypatch.setenv("OA_MSSQL_PASSWORD", "Sa#2026")
    report = db.check_mssql_status.invoke({"config_text": "host=10.0.0.10 port=1433 user=sa"})
    assert "❌ 连接失败: 登录失败，请检查用户名和密码" in report
    assert "SQL Server 服务已启动" in report
    assert runner.call_args.args[3] == "Sa#2026"


def test_mssql_status_full_report(monkeypatch) -> None:
    monkeypatch.setattr(
        db, "_exec_sqlcmd",
        Mock(side_effect=_mssql_router(
            backup="OA_Main|2026-05-01 03:00:00\nOA_Archive|NEVER\nOA_Legacy|2019-07-01 03:00:00",
        )),
    )

    report = db.check_mssql_status.invoke({"config_text": "host=10.0.0.10 port=1433 user=sa password=P@ssw0rd"})

    assert "✅ 连接成功 — Microsoft SQL Server 2019 (RTM-CU25) - 15.0.4360.2 (X64)" in report
    assert "🔗 活跃连接: 12 个" in report
    assert "   活跃会话: 15 个" in report
    assert "⚠️ 阻塞会话: 2 个（存在锁等待！）" in report
    assert "   OA_Main — 2048.0 MB" in report
    assert "✅ OA_Main: 2026-05-01 03:00:00" in report
    assert "🔴 OA_Archive: NEVER" in report
    assert "🟡 OA_Legacy: 2019-07-01 03:00:00" in report


@pytest.mark.parametrize("connections", ["12|15|0", "x|y|z", "only-two"])
def test_mssql_status_tolerates_connection_edge_cases(connections, monkeypatch) -> None:
    monkeypatch.setattr(db, "_exec_sqlcmd", Mock(side_effect=_mssql_router(connections=connections)))

    report = db.check_mssql_status.invoke({"config_text": "host=10.0.0.10 port=1433 user=sa password=x"})

    if connections == "12|15|0":
        assert "   阻塞会话: 0 个" in report
    elif connections == "x|y|z":
        # 第三列不是数字：int() 抛错被兜底，已输出的前缀保留，后续阻塞判定跳过
        assert "🔗 活跃连接: x 个" in report
        assert "阻塞会话" not in report
    else:
        assert "活跃连接" not in report


def test_mssql_status_swallows_optional_query_failures(monkeypatch) -> None:
    router = _mssql_router(size=RuntimeError("超时"), backup=RuntimeError("超时"))
    monkeypatch.setattr(db, "_exec_sqlcmd", Mock(side_effect=router))

    report = db.check_mssql_status.invoke({"config_text": "host=10.0.0.10 port=1433 user=sa password=x"})

    assert "✅ 连接成功" in report
    assert "数据库大小 TOP 5" not in report
    assert "最近备份时间" not in report


def test_mssql_status_skips_malformed_backup_rows(monkeypatch) -> None:
    monkeypatch.setattr(db, "_exec_sqlcmd", Mock(side_effect=_mssql_router(backup="OA_Main|NEVER\n无分隔符的行")))

    report = db.check_mssql_status.invoke({"config_text": "host=10.0.0.10 port=1433 user=sa password=x"})

    assert "🔴 OA_Main: NEVER" in report
    assert "无分隔符的行" not in report


# ---------- _exec_sqlplus / check_oracle_status ----------

def test_exec_sqlplus_writes_query_file_and_cleans_up(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(db, "IS_WINDOWS", False)
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    captured = {}

    def fake_run(cmd, **kwargs):
        sql_path = Path(cmd.split(" @", 1)[1])
        captured["cmd"] = cmd
        captured["path"] = sql_path
        captured["content"] = sql_path.read_text(encoding="utf-8")
        return _proc(stdout="VERSION:Oracle Database 19c\n")

    monkeypatch.setattr(subprocess, "run", fake_run)

    out = db._exec_sqlplus("db-ora01", 1521, "system", "Ora#2026", "SELECT 1 FROM dual", "orcl")

    assert out == "VERSION:Oracle Database 19c"
    assert "sqlplus -S system/Ora#2026@db-ora01:1521/orcl @" in captured["cmd"]
    assert captured["content"] == (
        "SET PAGESIZE 0 FEEDBACK OFF HEADING OFF LINESIZE 500;\nSELECT 1 FROM dual\nEXIT;\n"
    )
    assert not captured["path"].exists()


def test_exec_sqlplus_defaults_to_orcl_service(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    runner = Mock(return_value=_proc(stdout=""))
    monkeypatch.setattr(subprocess, "run", runner)

    db._exec_sqlplus("127.0.0.1", 1521, "system", "p", "SELECT 1")

    assert "@127.0.0.1:1521/orcl" in runner.call_args.args[0]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("ORA-01017: invalid username/password; logon denied", "__ERROR__: Oracle 登录失败，请检查用户名和密码"),
        ("ORA-12541: TNS:no listener", "__ERROR__: 无法连接到 ora01:1521，请检查监听器状态"),
        ("ORA-00942: table or view does not exist", "__ERROR__: ORA-00942: table or view does not exist"),
        ("SP2-0750: You may need to set ORACLE_HOME", "__ERROR__: sqlplus 无法解析连接字符串"),
        ("Unable to resolve service name", "__ERROR__: sqlplus 无法解析连接字符串"),
    ],
)
def test_exec_sqlplus_maps_oracle_errors(output, expected, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(subprocess, "run", Mock(return_value=_proc(stdout=output)))

    assert db._exec_sqlplus("ora01", 1521, "system", "p", "SELECT 1") == expected


def test_exec_sqlplus_timeout_and_missing_client_still_cleanup(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))

    monkeypatch.setattr(subprocess, "run", Mock(side_effect=subprocess.TimeoutExpired("sqlplus", 15)))
    assert db._exec_sqlplus("ora01", 1521, "system", "p", "SELECT 1") == "__ERROR__: 查询超时"
    assert list(tmp_path.iterdir()) == []

    monkeypatch.setattr(subprocess, "run", Mock(side_effect=FileNotFoundError("sqlplus")))
    assert db._exec_sqlplus("ora01", 1521, "system", "p", "SELECT 1") == (
        "__ERROR__: sqlplus 未安装，请安装 Oracle Instant Client"
    )


def test_oracle_status_requires_password_and_error_hint(monkeypatch) -> None:
    monkeypatch.delenv("OA_ORACLE_PASSWORD", raising=False)
    report = db.check_oracle_status.invoke({"config_text": "host=ora01 port=1521 user=system"})
    assert "未提供数据库密码" in report

    monkeypatch.setattr(db, "_exec_sqlplus", Mock(return_value="__ERROR__: 无法连接到 ora01:1521，请检查监听器状态"))
    report = db.check_oracle_status.invoke({"config_text": "host=ora01 port=1521 user=system password=x service=oa"})
    assert "❌ 连接失败: 无法连接到 ora01:1521" in report
    assert "lsnrctl status" in report


def test_oracle_status_full_report(monkeypatch) -> None:
    router = _oracle_router(
        tablespace=(
            "SYSTEM|1024MB|2048MB|50%|OK\n"
            "TEMP|900MB|1000MB|90.5%|CRITICAL\n"
            "USERS|850MB|1000MB|85%|WARNING"
        ),
        archive=(
            "LOG_MODE: ARCHIVELOG\n"
            "ARCHIVE_DEST: LOG_ARCHIVE_DEST_1 | STATUS: VALID\n"
            "ARCHIVE_DEST: LOG_ARCHIVE_DEST_2 | STATUS: ERROR\n"
            "ARCHIVE_DEST: LOG_ARCHIVE_DEST_3 | STATUS: DEFERRED"
        ),
    )
    monkeypatch.setattr(db, "_exec_sqlplus", Mock(side_effect=router))

    report = db.check_oracle_status.invoke(
        {"config_text": "host=ora01 port=1521 user=system password=x service=oa"}
    )

    assert "目标: ora01:1521/oa" in report
    assert "✅ VERSION:Oracle Database 19c Enterprise Edition" in report
    assert "📋 INSTANCE:ORCL | STATUS:OPEN | HOST:oa-db01" in report
    assert "⏱️ STARTUP:2026-01-02 09:30:00" in report
    assert "   ✅ SYSTEM: 1024MB/2048MB (50%)" in report
    assert "   🔴 TEMP: 900MB/1000MB (90.5%)" in report
    assert "   🟡 USERS: 850MB/1000MB (85%)" in report
    assert "   37 total sessions" in report
    assert "✅ LOG_MODE: ARCHIVELOG" in report
    assert "✅ ARCHIVE_DEST: LOG_ARCHIVE_DEST_1 | STATUS: VALID" in report
    assert "🔴 ARCHIVE_DEST: LOG_ARCHIVE_DEST_2 | STATUS: ERROR" in report
    assert "   ARCHIVE_DEST: LOG_ARCHIVE_DEST_3 | STATUS: DEFERRED" in report


def test_oracle_status_misreports_noarchivelog_as_safe(monkeypatch) -> None:
    """已知缺陷（随交付汇报）：判定用 "ARCHIVELOG" in row.upper() 做子串匹配，
    NOARCHIVELOG 同样命中，未开归档被误报为 ✅，🔴 分支对真实 log_mode 不可达。"""
    router = _oracle_router(
        sessions="37 total sessions\nORA-00942: table or view does not exist",
        archive="LOG_MODE: NOARCHIVELOG",
    )
    monkeypatch.setattr(db, "_exec_sqlplus", Mock(side_effect=router))

    report = db.check_oracle_status.invoke({"config_text": "host=ora01 user=system password=x"})

    assert "✅ LOG_MODE: NOARCHIVELOG" in report
    assert "建议开启归档模式" not in report
    assert "ORA-00942" not in report
    assert "目标: ora01:1521/orcl" in report


def test_oracle_status_skips_sections_on_error_payloads(monkeypatch) -> None:
    router = _oracle_router(
        version="VERSION:Oracle Database 19c\n\nIDLE> disconnected",
        tablespace="__ERROR__: 查询超时",
        sessions="__ERROR__: 查询超时",
        archive="__ERROR__: 查询超时",
    )
    monkeypatch.setattr(db, "_exec_sqlplus", Mock(side_effect=router))

    report = db.check_oracle_status.invoke({"config_text": "host=ora01 user=system password=x"})

    assert "✅ VERSION:Oracle Database 19c" in report
    assert "📊 表空间使用率:" not in report
    assert "🔗 会话统计:" not in report
    assert "LOG_MODE" not in report


def test_oracle_status_swallows_optional_query_failures(monkeypatch) -> None:
    router = _oracle_router(
        tablespace=RuntimeError("ORA-00942"), sessions=RuntimeError("ORA-00942"), archive=RuntimeError("ORA-00942"),
    )
    monkeypatch.setattr(db, "_exec_sqlplus", Mock(side_effect=router))

    report = db.check_oracle_status.invoke({"config_text": "host=ora01 user=system password=x"})

    assert "✅ VERSION:Oracle Database 19c" in report
    assert "📊 表空间使用率:" not in report
    assert "🔗 会话统计:" not in report


# ---------- DBCheckAgent ----------

@pytest.mark.parametrize("api_key", ["", "ollama", "your-api-key-here"])
def test_db_agent_skips_llm_without_real_key(api_key) -> None:
    assert db.DBCheckAgent(llm_api_key=api_key)._agent is None


def test_db_agent_initializes_llm_agent(monkeypatch) -> None:
    chat_cls = Mock(name="ChatOpenAI", return_value=Mock(name="llm"))
    create_agent = Mock(name="create_agent")
    monkeypatch.setattr("langchain_openai.ChatOpenAI", chat_cls, raising=False)
    monkeypatch.setattr("langchain.agents.create_agent", create_agent, raising=False)

    agent = db.DBCheckAgent(llm_api_key="sk-oa-test", llm_model="deepseek-chat")

    assert agent._agent is create_agent.return_value
    assert chat_cls.call_args.kwargs["temperature"] == 0.2
    assert chat_cls.call_args.kwargs["model"] == "deepseek-chat"


def test_db_agent_degrades_when_llm_init_fails(monkeypatch) -> None:
    monkeypatch.setattr("langchain_openai.ChatOpenAI", Mock(side_effect=RuntimeError("连通性失败")), raising=False)

    agent = db.DBCheckAgent(llm_api_key="sk-oa-test")

    assert agent._agent is None


def test_db_agent_returns_llm_answer_for_mysql(monkeypatch) -> None:
    agent = db.DBCheckAgent()
    agent._agent = Mock()
    agent._agent.invoke.return_value = {"messages": [SimpleNamespace(content="MySQL 巡检摘要：连接正常")]}

    assert agent.check_mysql("db01", 3307, "ops", "p") == "MySQL 巡检摘要：连接正常"
    prompt = agent._agent.invoke.call_args.args[0]["messages"][0]["content"]
    assert "db01:3307" in prompt


def test_db_agent_falls_back_when_llm_call_fails(monkeypatch) -> None:
    agent = db.DBCheckAgent()
    agent._agent = Mock()
    agent._agent.invoke.side_effect = RuntimeError("LLM 超时")
    monkeypatch.setattr(db, "_exec_sqlcmd", Mock(return_value="__ERROR__: 无法连接到 10.0.0.10:1433"))

    report = agent.check_mssql("10.0.0.10", 1433, "sa", "p")

    assert "❌ 连接失败: 无法连接到 10.0.0.10:1433" in report


def test_db_agent_falls_back_on_empty_messages(monkeypatch) -> None:
    agent = db.DBCheckAgent()
    agent._agent = Mock()
    agent._agent.invoke.return_value = {"messages": []}
    monkeypatch.setattr(db, "_exec_sql", Mock(return_value="__ERROR__: 查询超时"))

    assert "❌ 连接失败: 查询超时" in agent.check_mysql("db01", 3306, "ops", "p")


def test_db_agent_without_llm_calls_local_tools(monkeypatch) -> None:
    monkeypatch.setattr(db, "_exec_sql", Mock(return_value="__ERROR__: 查询超时"))
    monkeypatch.setattr(db, "_exec_sqlcmd", Mock(return_value="__ERROR__: 查询超时"))

    agent = db.DBCheckAgent()

    assert agent._agent is None
    assert "❌ 连接失败: 查询超时" in agent.check_mysql("db01", 3306, "ops", "p")
    assert "❌ 连接失败: 查询超时" in agent.check_mssql("10.0.0.10", 1433, "sa", "p")


def test_db_agent_degrades_on_llm_error_for_mysql(monkeypatch) -> None:
    agent = db.DBCheckAgent()
    agent._agent = Mock()
    agent._agent.invoke.side_effect = RuntimeError("LLM 超时")
    monkeypatch.setattr(db, "_exec_sql", Mock(return_value="__ERROR__: 查询超时"))

    assert "❌ 连接失败: 查询超时" in agent.check_mysql("db01", 3306, "ops", "p")


def test_db_agent_falls_back_on_empty_messages_for_mssql(monkeypatch) -> None:
    agent = db.DBCheckAgent()
    agent._agent = Mock()
    agent._agent.invoke.return_value = {"messages": []}
    monkeypatch.setattr(db, "_exec_sqlcmd", Mock(return_value="__ERROR__: 登录失败，请检查用户名和密码"))

    assert "❌ 连接失败: 登录失败，请检查用户名和密码" in agent.check_mssql("10.0.0.10", 1433, "sa", "p")


def test_db_agent_delegates_redis_oracle_and_slow_queries(monkeypatch) -> None:
    redis_runner = Mock(side_effect=["PONG", _redis_info()])
    monkeypatch.setattr(db, "_exec_redis", redis_runner)
    sqlplus_runner = Mock(side_effect=_oracle_router())
    monkeypatch.setattr(db, "_exec_sqlplus", sqlplus_runner)
    slow_runner = Mock(return_value="")
    monkeypatch.setattr(db, "_exec_sql", slow_runner)

    agent = db.DBCheckAgent()

    assert "目标: redis01:6380" in agent.check_redis("redis01", 6380, "s3cret")
    assert redis_runner.call_args_list[0].args == ("redis01", 6380, "s3cret", "PING")
    assert "Oracle 数据库健康巡检报告" in agent.check_oracle("ora01", 1521, "system", "p", service="oa")
    assert sqlplus_runner.call_args.args[5] == "oa"
    assert agent.show_slow_queries("db01", 3306, "ops", "p", limit=3) == "✅ 近24小时无慢查询记录"
    assert "LIMIT 3" in slow_runner.call_args.args[4]
