from types import SimpleNamespace
from unittest.mock import Mock

import utils.database as databases
from utils.database import Database


def test_conversation_messages_persist_in_temporary_database(tmp_path) -> None:
    database = Database(tmp_path / "test.db")
    conversation_id = database.create_conversation("故障排查")
    database.save_message(conversation_id, "user", "OA 无法登录")
    database.save_message(conversation_id, "assistant", "先检查服务状态")

    assert [message["role"] for message in database.get_conversation_messages(conversation_id)] == [
        "user", "assistant"
    ]
    assert database.list_conversations()[0]["message_count"] == 2
    database.close()


def test_inspection_record_persists_in_temporary_database(tmp_path) -> None:
    database = Database(tmp_path / "inspection.db")
    database.save_inspection(
        check_type="disk",
        check_type_cn="磁盘",
        target="/data",
        result="使用率 88%",
        status="warning",
        is_simulated=False,
    )

    history = database.get_inspection_history(days=1)

    assert len(history) == 1
    assert history[0]["target"] == "/data"
    assert history[0]["status"] == "warning"
    assert history[0]["is_simulated"] == 0
    database.close()


def test_data_directory_environment_isolates_default_database(tmp_path, monkeypatch) -> None:
    original_instance = Database._instance
    monkeypatch.setenv("OA_DATA_DIR", str(tmp_path))
    Database._instance = None

    try:
        database = Database()

        assert database.get_db_stats()["db_path"] == str((tmp_path / "oa_ops.db").resolve())
        database.close()
    finally:
        Database._instance = original_instance


# ========== 追加：单例、路径解析、巡检/告警/日志/对话全链路 ==========


def test_singleton_returns_existing_instance(tmp_path, monkeypatch) -> None:
    original_instance = Database._instance
    monkeypatch.setenv("OA_DATA_DIR", str(tmp_path))
    Database._instance = None
    try:
        first = Database()
        second = Database()

        assert first is second
        assert first._db_path == second._db_path
        first.close()
    finally:
        Database._instance = original_instance


def test_database_resolves_relative_path_from_app_root(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OA_DATA_DIR", raising=False)
    monkeypatch.setattr(
        databases, "config", SimpleNamespace(get=lambda path, default=None: "data/oa_ops.db")
    )
    monkeypatch.setattr(databases, "get_app_root", lambda: str(tmp_path / "approot"))
    original_instance = Database._instance
    Database._instance = None
    try:
        database = Database()
        try:
            expected = (tmp_path / "approot" / "data" / "oa_ops.db").resolve()
            assert database._db_path == str(expected)
        finally:
            database.close()
    finally:
        Database._instance = original_instance


def test_database_honours_absolute_configured_path(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OA_DATA_DIR", raising=False)
    target = tmp_path / "abs-data" / "oa_ops.db"
    monkeypatch.setattr(
        databases, "config", SimpleNamespace(get=lambda path, default=None: str(target))
    )
    monkeypatch.setattr(databases, "get_app_root", lambda: str(tmp_path / "unused"))
    original_instance = Database._instance
    Database._instance = None
    try:
        database = Database()
        try:
            assert database._db_path == str(target.resolve())
        finally:
            database.close()
    finally:
        Database._instance = original_instance


def test_inspection_batch_summary_and_filters(tmp_path) -> None:
    database = Database(tmp_path / "inspection-batch.db")
    saved = database.save_inspection_batch(
        [
            {
                "check_type": "disk",
                "check_type_cn": "磁盘",
                "target": "/",
                "result": "使用率 72%",
                "status": "normal",
                "is_simulated": 0,
            },
            {
                "check_type": "disk",
                "check_type_cn": "磁盘",
                "target": "/data",
                "result": "使用率 91%",
                "status": "warning",
                "is_simulated": 0,
            },
            {
                "check_type": "memory",
                "check_type_cn": "内存",
                "target": "",
                "result": "使用率 62%",
                "status": "normal",
            },
        ]
    )

    assert saved == 3

    summary = database.get_inspection_summary(days=1)
    assert summary["total"] == 3
    assert summary["normal"] == 2
    assert summary["warning"] == 1
    assert summary["error"] == 0
    assert summary["by_type"]["磁盘"] == {"total": 2, "abnormal": 1}
    assert summary["by_type"]["内存"] == {"total": 1, "abnormal": 0}

    disk_warnings = database.get_inspection_history(days=1, check_type="disk", status="warning")
    assert len(disk_warnings) == 1
    assert disk_warnings[0]["target"] == "/data"
    assert len(database.get_inspection_history(days=1, check_type="memory")) == 1
    assert database.get_inspection_history(days=1, status="error") == []
    assert len(database.get_inspection_history(days=1, limit=2)) == 2
    database.close()


def test_inspection_summary_of_empty_database(tmp_path) -> None:
    database = Database(tmp_path / "empty.db")

    assert database.get_inspection_summary(days=1) == {
        "total": 0,
        "normal": 0,
        "warning": 0,
        "error": 0,
        "by_type": {},
    }
    database.close()


def test_alert_lifecycle(tmp_path) -> None:
    database = Database(tmp_path / "alerts.db")
    alert_id = database.save_alert("inspection", "critical", "磁盘使用率超阈值", "/data 使用率 91%")

    assert alert_id > 0
    pending = database.get_pending_alerts()
    assert [row["title"] for row in pending] == ["磁盘使用率超阈值"]
    assert pending[0]["notified"] == 0

    database.mark_alert_notified(alert_id, channel="dingtalk")

    assert database.get_pending_alerts() == []
    history = database.get_alert_history(days=1)
    assert len(history) == 1
    assert history[0]["notified"] == 1
    assert history[0]["notify_channel"] == "dingtalk"
    assert history[0]["severity"] == "critical"
    assert database.get_alert_history(days=1, limit=1)[0]["id"] == alert_id
    database.close()


def test_log_analysis_history_roundtrip(tmp_path) -> None:
    database = Database(tmp_path / "logs.db")
    record_id = database.save_log_analysis(
        log_source="data/inspection_logs/oa.log",
        log_size=2048,
        faults_found=3,
        severe_count=1,
        high_count=1,
        medium_count=1,
        report="发现 3 个故障",
    )

    assert record_id > 0
    history = database.get_log_analysis_history(days=1)
    assert len(history) == 1
    assert history[0]["log_source"].endswith("oa.log")
    assert history[0]["faults_found"] == 3
    assert "report" not in history[0]
    database.close()


def test_conversation_rename_and_delete(tmp_path) -> None:
    database = Database(tmp_path / "chat.db")
    conv_id = database.create_conversation("OA 登录故障")
    database.save_message(conv_id, "user", "OA 登录报 500")
    database.save_message(conv_id, "assistant", "请检查 Tomcat 日志")

    assert database.rename_conversation(conv_id, "OA 登录 500 排查") is True
    listed = database.list_conversations()
    assert listed[0]["title"] == "OA 登录 500 排查"
    assert listed[0]["preview"] == "OA 登录报 500"

    assert database.rename_conversation("missing-conv", "不存在的会话") is False
    assert database.delete_conversation(conv_id) is True
    assert database.delete_conversation(conv_id) is False
    assert database.get_conversation_messages(conv_id) == []
    assert database.list_conversations() == []
    database.close()


def test_database_stats_vacuum_and_close_paths(tmp_path) -> None:
    database = Database(tmp_path / "stats.db")
    database.save_inspection(
        check_type="ports",
        check_type_cn="端口检测",
        target="80",
        result="[正常] 端口 80 监听中",
    )

    stats = database.get_db_stats()
    assert stats["inspection_records"] == 1
    assert stats["conversations"] == 0
    assert stats["db_path"].endswith("stats.db")

    database.vacuum()
    database.close()
    database.close()
    assert database._conn_local.conn is None

    database._conn_local.conn = Mock(close=Mock(side_effect=RuntimeError("句柄已释放")))
    database.close()
    assert database._conn_local.conn is None


def test_close_all_checkpoints_wal_and_swallows_errors(tmp_path) -> None:
    database = Database(tmp_path / "close-all.db")
    database.save_inspection(
        check_type="memory",
        check_type_cn="内存",
        target="",
        result="[正常] 使用率 62%",
    )

    database.close_all()
    assert database._conn_local.conn is None

    database._conn_local.conn = Mock(execute=Mock(side_effect=RuntimeError("WAL 被占用")))
    database.close_all()
    assert database._conn_local.conn is None

    database.close_all()
    assert database._conn_local.conn is None


# ========== 根因诊断记录（第 4 周） ==========


def test_incident_report_roundtrip(tmp_path) -> None:
    database = Database(tmp_path / "incidents.db")
    report = {
        "incident_id": "INC-TEST-1",
        "created_at": "2026-09-18 10:00:00",
        "status": "ok",
        "root_cause": {"cause_id": "disk_full", "title": "服务器磁盘空间不足", "score": 0.87},
        "confidence": 0.87,
        "events": [
            {
                "id": "EVT-1",
                "timestamp": "2026-09-18 09:12:44",
                "source": "log",
                "service": "",
                "host": "",
                "metric": "resource",
                "severity": "critical",
                "raw": "No space left on device",
                "tags": [],
            }
        ],
    }
    incident_id = database.save_incident(report, service="oa-web", host="10.20.1.11", source="api")
    assert incident_id == "INC-TEST-1"

    loaded = database.get_incident("INC-TEST-1")
    assert loaded is not None
    assert loaded["status"] == "ok"
    assert loaded["root_cause"] == "disk_full"
    assert loaded["root_title"] == "服务器磁盘空间不足"
    assert loaded["service"] == "oa-web"
    assert abs(float(loaded["confidence"]) - 0.87) < 1e-9
    assert loaded["report"]["root_cause"]["cause_id"] == "disk_full"
    assert loaded["events"][0]["raw"] == "No space left on device"
    assert "report_json" not in loaded
    database.close()


def test_incident_missing_returns_none(tmp_path) -> None:
    database = Database(tmp_path / "incidents-missing.db")
    assert database.get_incident("INC-NOPE") is None
    database.close()


def test_incident_list_order_and_uncertain(tmp_path) -> None:
    database = Database(tmp_path / "incidents-list.db")
    database.save_incident(
        {
            "incident_id": "INC-A",
            "created_at": "2026-09-18 10:00:00",
            "status": "uncertain",
            "root_cause": None,
            "confidence": 0.0,
        }
    )
    database.save_incident(
        {
            "incident_id": "INC-B",
            "created_at": "2026-09-18 11:00:00",
            "status": "ok",
            "root_cause": {"cause_id": "oom", "title": "应用内存溢出（OOM）"},
            "confidence": 0.9,
        }
    )
    rows = database.list_incidents(limit=10)
    assert [row["id"] for row in rows] == ["INC-B", "INC-A"]
    assert rows[1]["root_cause"] == ""
    assert rows[1]["status"] == "uncertain"
    database.close()


def test_incident_same_id_overwrites(tmp_path) -> None:
    database = Database(tmp_path / "incidents-overwrite.db")
    base = {
        "incident_id": "INC-SAME",
        "created_at": "2026-09-18 10:00:00",
        "status": "uncertain",
        "root_cause": None,
        "confidence": 0.0,
    }
    database.save_incident(base)
    database.save_incident(
        {**base, "status": "ok", "confidence": 0.8, "root_cause": {"cause_id": "disk_full", "title": "磁盘不足"}}
    )
    rows = database.list_incidents()
    assert len(rows) == 1
    assert rows[0]["status"] == "ok"
    database.close()
