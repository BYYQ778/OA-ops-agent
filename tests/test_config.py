"""离线测试：utils.config 的加载顺序、点号路径访问与 config.yaml 原地改写。

注意：utils/__init__.py 会把 ``config`` 绑定为 Config 实例，直接 monkeypatch
"utils.config.*" 字符串路径会打到实例上；这里用 importlib 取真正的模块对象。
"""

import importlib
import os
import sys
from pathlib import Path

cfg = importlib.import_module("utils.config")


def test_get_app_root_uses_project_dir_or_frozen_exe(tmp_path, monkeypatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    config_file = cfg.__file__
    assert config_file is not None
    expected = os.path.dirname(os.path.dirname(os.path.abspath(config_file)))
    assert cfg.get_app_root() == expected

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "dist" / "OA运维Agent.exe"))
    assert cfg.get_app_root() == str(tmp_path / "dist")


def test_dotenv_loading_is_skipped_when_file_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cfg, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("OA_TEST_DOTENV_MISSING", raising=False)

    cfg._load_dotenv()

    assert "OA_TEST_DOTENV_MISSING" not in os.environ


def test_dotenv_loading_parses_lines_and_keeps_env_priority(tmp_path, monkeypatch) -> None:
    (tmp_path / ".env").write_text(
        "# OA 运维Agent 密钥文件\n"
        "\n"
        'OA_TEST_DOTENV_NEW="quoted-value"\n'
        "OA_TEST_DOTENV_SINGLE='single-value'\n"
        "OA_TEST_DOTENV_EXISTING=from-file\n"
        "NOT_A_PAIR\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OA_TEST_DOTENV_EXISTING", "from-system")
    monkeypatch.delenv("OA_TEST_DOTENV_NEW", raising=False)
    monkeypatch.delenv("OA_TEST_DOTENV_SINGLE", raising=False)

    cfg._load_dotenv()

    assert os.environ["OA_TEST_DOTENV_NEW"] == "quoted-value"
    assert os.environ["OA_TEST_DOTENV_SINGLE"] == "single-value"
    assert os.environ["OA_TEST_DOTENV_EXISTING"] == "from-system"


def test_config_is_a_singleton_and_all_returns_a_copy() -> None:
    assert cfg.Config() is cfg.config

    snapshot = cfg.config.all()
    snapshot["runner_test"] = "临时值"

    assert "runner_test" not in cfg.config.all()


def test_load_without_config_yaml_yields_empty_data(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cfg, "PROJECT_ROOT", str(tmp_path))
    original_instance = cfg.Config._instance
    cfg.Config._instance = None
    try:
        fresh = cfg.Config()

        assert fresh is not cfg.config
        assert fresh.all() == {}
    finally:
        cfg.Config._instance = original_instance


def test_resolve_env_vars_supports_defaults(monkeypatch) -> None:
    monkeypatch.setenv("OA_TEST_HOST", "oa.example.com")
    monkeypatch.delenv("OA_TEST_UNSET", raising=False)

    resolved = cfg.config._resolve_env_vars(
        "host: ${OA_TEST_HOST}\nport: ${OA_TEST_PORT:7860}\nempty: ${OA_TEST_UNSET}"
    )

    assert "host: oa.example.com" in resolved
    assert "port: 7860" in resolved
    assert resolved.endswith("empty: ")


def test_get_walks_dotted_paths_and_falls_back(monkeypatch) -> None:
    monkeypatch.setattr(
        cfg.config, "_data", {"llm": {"api_key": "sk-oa-test"}, "server": {"port": 7860}}
    )

    assert cfg.config.get("llm.api_key") == "sk-oa-test"
    assert cfg.config.get("server.port") == 7860
    assert cfg.config.get("llm.api_key.length", "默认值") == "默认值"
    assert cfg.config.get("missing.section") is None


def test_set_creates_missing_sections(monkeypatch) -> None:
    monkeypatch.setattr(cfg.config, "_data", {})

    cfg.config.set("ssl_monitor.domains", ["oa.example.com"])
    cfg.config.set("ssl_monitor.interval_hours", 6)
    cfg.config.set("ssl_monitor.domains", ["oa.example.com", "www.example.com"])

    assert cfg.config.get("ssl_monitor.domains") == ["oa.example.com", "www.example.com"]
    assert cfg.config.get("ssl_monitor.interval_hours") == 6


def test_replace_in_lines_keeps_inline_comment() -> None:
    lines = ["llm:\n", "  provider: ollama  # 离线优先\n", "server:\n", "  port: 7860\n"]

    assert cfg.config._replace_in_lines(lines, ["llm", "provider"], "deepseek") is True

    assert lines[1] == "  provider: deepseek  # 离线优先\n"
    assert lines[3] == "  port: 7860\n"


def test_replace_in_lines_skips_comments_and_blank_lines() -> None:
    lines = ["# 顶层注释\n", "\n", "llm:\n", "  # 密钥只放 .env\n", "\n", "  provider: ollama\n"]

    assert cfg.config._replace_in_lines(lines, ["llm", "provider"], "deepseek") is True

    assert lines[-1] == "  provider: deepseek\n"


def test_replace_in_lines_inserts_missing_key_before_next_section() -> None:
    lines = ["llm:\n", "  provider: ollama\n", "server:\n", "  port: 7860\n"]

    assert cfg.config._replace_in_lines(lines, ["llm", "model"], "qwen3:8b") is True

    assert lines == [
        "llm:\n",
        "  provider: ollama\n",
        "  model: qwen3:8b\n",
        "server:\n",
        "  port: 7860\n",
    ]


def test_replace_in_lines_appends_at_end_of_file() -> None:
    lines = ["llm:\n", "  provider: ollama\n"]

    assert cfg.config._replace_in_lines(lines, ["llm", "temperature"], "0.2") is True

    assert lines[-1] == "\n  temperature: 0.2\n"


def test_replace_in_lines_reports_missing_parent_section() -> None:
    lines = ["llm:\n", "  provider: ollama\n"]

    assert cfg.config._replace_in_lines(lines, ["storage", "path"], "/data/kg") is False

    assert lines == ["llm:\n", "  provider: ollama\n"]


def test_replace_in_lines_skips_unrelated_lines_at_target_indent() -> None:
    lines = ["- 平台清单\n", "llm:\n", "  provider: ollama\n"]

    assert cfg.config._replace_in_lines(lines, ["llm", "provider"], "deepseek") is True

    assert lines == ["- 平台清单\n", "llm:\n", "  provider: deepseek\n"]


def test_replace_in_lines_walks_past_deeper_nested_keys() -> None:
    lines = ["llm:\n", "  retry:\n", "    times: 3\n", "  provider: ollama\n"]

    assert cfg.config._replace_in_lines(lines, ["llm", "provider"], "deepseek") is True

    assert lines == ["llm:\n", "  retry:\n", "    times: 3\n", "  provider: deepseek\n"]


def _write_sample_yaml(path: Path) -> None:
    path.write_text(
        "# OA 运维Agent 配置\n"
        "llm:\n"
        "  provider: ollama  # 离线优先\n"
        "  api_key: ${DEEPSEEK_API_KEY:}\n"
        "server:\n"
        "  port: 7860\n",
        encoding="utf-8",
    )


def test_update_file_rewrites_value_and_reloads(tmp_path, monkeypatch) -> None:
    config_yaml = tmp_path / "config.yaml"
    _write_sample_yaml(config_yaml)
    monkeypatch.setattr(cfg, "PROJECT_ROOT", str(tmp_path))
    original_data = cfg.config._data
    try:
        assert cfg.config.update_file({"llm.provider": "deepseek"}) is True
        assert cfg.config.get("llm.provider") == "deepseek"
    finally:
        cfg.config._data = original_data

    text = config_yaml.read_text(encoding="utf-8")
    assert "provider: deepseek  # 离线优先" in text
    assert "# OA 运维Agent 配置" in text
    assert "port: 7860" in text


def test_update_file_inserts_missing_key_inside_section(tmp_path, monkeypatch) -> None:
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("llm:\n  provider: ollama\nserver:\n  port: 7860\n", encoding="utf-8")
    monkeypatch.setattr(cfg, "PROJECT_ROOT", str(tmp_path))
    original_data = cfg.config._data
    try:
        assert cfg.config.update_file({"llm.model": "qwen3:8b"}) is True
    finally:
        cfg.config._data = original_data

    text = config_yaml.read_text(encoding="utf-8")
    assert "  model: qwen3:8b\n" in text
    assert text.index("  model: qwen3:8b") < text.index("server:")


def test_update_file_fails_cleanly_for_unknown_section(tmp_path, monkeypatch) -> None:
    config_yaml = tmp_path / "config.yaml"
    _write_sample_yaml(config_yaml)
    monkeypatch.setattr(cfg, "PROJECT_ROOT", str(tmp_path))
    original_data = cfg.config._data

    assert cfg.config.update_file({"storage.dir": "/data/kg"}) is False

    assert cfg.config._data is original_data
    assert "storage" not in config_yaml.read_text(encoding="utf-8")


def test_reload_picks_up_yaml_edits_and_env_placeholders(tmp_path, monkeypatch) -> None:
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text(
        "server:\n  port: 7861\nllm:\n  api_key: ${OA_TEST_RELOAD_KEY:default-token}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("OA_TEST_RELOAD_KEY", raising=False)
    original_data = cfg.config._data
    try:
        cfg.config.reload()

        assert cfg.config.get("server.port") == 7861
        assert cfg.config.get("llm.api_key") == "default-token"
    finally:
        cfg.config._data = original_data
