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
