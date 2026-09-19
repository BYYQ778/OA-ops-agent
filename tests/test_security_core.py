"""第 5 周 w5-4：安全基础（密码哈希/会话令牌/用户解析/登录限速/启动门禁/会话存储）。"""

from __future__ import annotations

import runpy
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from utils.database import Database
from utils.security import (
    AuthUser,
    LoginRateLimiter,
    SecurityGateError,
    auth_enabled,
    authenticate,
    bypass_loopback,
    enforce_startup_security,
    evaluate_startup_security,
    generate_session_token,
    hash_password,
    hash_session_token,
    is_loopback_host,
    load_users,
    session_ttl_seconds,
    verify_password,
)

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hash_password.py"


class _Config:
    """最小 config 替身（点号路径 get，与 utils.config.Config.get 同语义）。"""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def get(self, path: str, default: Any = None) -> Any:
        value: Any = self._data
        for key in path.split("."):
            if not isinstance(value, dict):
                return default
            value = value.get(key)
            if value is None:
                return default
        return value


def _auth_config(**overrides: Any) -> _Config:
    data: dict[str, Any] = {"auth": {"enabled": True, "username": "admin", "password": "admin123"}}
    data["auth"].update(overrides)
    return _Config(data)


# ---------- 密码哈希 ----------


def test_hash_verify_roundtrip_with_non_ascii() -> None:
    hashed = hash_password("S3cret-Pass!中文密码")
    assert hashed.startswith("pbkdf2_sha256$")
    assert verify_password("S3cret-Pass!中文密码", hashed) is True
    assert verify_password("wrong", hashed) is False


def test_hash_unique_salt_and_custom_iterations() -> None:
    assert hash_password("same") != hash_password("same")
    hashed = hash_password("pw", iterations=1000)
    assert "$1000$" in hashed
    assert verify_password("pw", hashed) is True


def test_verify_plaintext_and_malformed() -> None:
    assert verify_password("plain-pass", "plain-pass") is True
    assert verify_password("plain-pass", "other") is False
    assert verify_password("x", "pbkdf2_sha256$bad") is False
    assert verify_password("x", "pbkdf2_sha256$not-int$zz$zz") is False
    assert verify_password("x", "") is False


# ---------- 令牌 ----------


def test_session_token_generate_and_hash() -> None:
    token = generate_session_token()
    assert len(token) >= 32
    assert generate_session_token() != token
    digest = hash_session_token(token)
    assert digest != token and len(digest) == 64
    assert hash_session_token(token) == digest


# ---------- 主机判定与开关 ----------


def test_is_loopback_host() -> None:
    assert is_loopback_host("127.0.0.1") is True
    assert is_loopback_host("127.5.5.5") is True
    assert is_loopback_host("::1") is True
    assert is_loopback_host("localhost") is True
    assert is_loopback_host("testclient") is True
    assert is_loopback_host("0.0.0.0") is False
    assert is_loopback_host("192.168.1.10") is False
    assert is_loopback_host("") is False


def test_auth_flag_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _auth_config(enabled=False)
    assert auth_enabled(config) is False
    monkeypatch.setenv("OA_AUTH_ENABLED", "1")
    assert auth_enabled(config) is True


def test_bypass_loopback_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    assert bypass_loopback(_auth_config(bypass_loopback=True)) is True
    monkeypatch.setenv("OA_AUTH_BYPASS_LOOPBACK", "0")
    assert bypass_loopback(_auth_config(bypass_loopback=True)) is False
    monkeypatch.delenv("OA_AUTH_BYPASS_LOOPBACK")
    assert bypass_loopback(_Config({"auth": {}})) is True  # 缺省 True


def test_session_ttl_seconds() -> None:
    assert session_ttl_seconds(_Config({"auth": {"session_ttl_hours": 2}})) == 7200.0
    assert session_ttl_seconds(_Config({})) == 12 * 3600.0


# ---------- 用户解析与认证 ----------


def test_load_users_legacy_single() -> None:
    users = load_users(_auth_config())
    assert len(users) == 1
    assert users[0].username == "admin" and users[0].role == "admin"


def test_load_users_list_and_role_normalize() -> None:
    config = _auth_config(
        users=[
            {"username": "ops", "password": "x" * 12, "role": "ADMIN"},
            {"username": "guest", "password_hash": hash_password("y" * 12), "role": "whatever"},
            {"username": "", "password": "z"},
            "not-a-dict",
        ]
    )
    users = load_users(config)
    assert [u.username for u in users] == ["ops", "guest"]
    assert users[0].role == "admin" and users[1].role == "viewer"


def test_authenticate_paths() -> None:
    users = [
        AuthUser(username="admin", password="strong-pass-1", role="admin"),
        AuthUser(username="hashed", password_hash=hash_password("strong-pass-2"), role="viewer"),
    ]
    admin = authenticate(users, "admin", "strong-pass-1")
    assert admin is not None and admin.username == "admin"
    hashed_user = authenticate(users, "hashed", "strong-pass-2")
    assert hashed_user is not None and hashed_user.role == "viewer"
    assert authenticate(users, "admin", "bad") is None
    assert authenticate(users, "nobody", "x") is None
    empty = [AuthUser(username="none", password="", role="admin")]
    assert authenticate(empty, "none", "") is None  # 空密码不可登录


# ---------- 登录限速 ----------


def test_login_rate_limiter_window_and_reset() -> None:
    limiter = LoginRateLimiter(max_attempts=3, window_seconds=60)
    now = 1000.0
    for offset in range(3):
        assert limiter.allow("10.0.0.9", now + offset) is True
        limiter.record_failure("10.0.0.9", now + offset)
    assert limiter.allow("10.0.0.9", now + 3) is False
    assert limiter.allow("10.0.0.9", now + 61) is True
    limiter.reset("10.0.0.9")
    assert limiter.allow("10.0.0.9", now + 3) is True


# ---------- 启动门禁 ----------


def test_gate_loopback_default_password_warns_only() -> None:
    issues = evaluate_startup_security(_auth_config(), "127.0.0.1")
    assert len(issues) == 1 and issues[0].level == "warning"
    enforce_startup_security(_auth_config(), "127.0.0.1")  # 不抛


def test_gate_non_loopback_blocks() -> None:
    for config in (_auth_config(enabled=False), _auth_config(password=""), _auth_config(password="admin123")):
        with pytest.raises(SecurityGateError):
            enforce_startup_security(config, "0.0.0.0")


def test_gate_non_loopback_strong_credentials_pass() -> None:
    assert evaluate_startup_security(_auth_config(password="Str0ng-!-Passw0rd"), "0.0.0.0") == []
    hashed_config = _auth_config(
        users=[{"username": "admin", "password_hash": hash_password("Str0ng-Passw0rd"), "role": "admin"}]
    )
    assert evaluate_startup_security(hashed_config, "0.0.0.0") == []


# ---------- 会话存储（SQLite） ----------


def test_session_store_lifecycle(tmp_path: Path) -> None:
    store = Database(tmp_path / "auth.db")
    token_hash = hash_session_token(generate_session_token())
    store.create_session(token_hash, "admin", "admin", ttl_seconds=60, client="10.0.0.9")
    record = store.get_session(token_hash, now=time.time())
    assert record is not None and record["username"] == "admin" and record["role"] == "admin"

    assert store.get_session(token_hash, now=time.time() + 61) is None
    assert store.get_session(token_hash) is None  # 过期即删

    token2 = hash_session_token(generate_session_token())
    store.create_session(token2, "admin", "admin", ttl_seconds=60)
    assert store.delete_session(token2) is True
    assert store.delete_session(token2) is False


def test_session_cleanup_and_auth_events(tmp_path: Path) -> None:
    store = Database(tmp_path / "auth2.db")
    alive = hash_session_token(generate_session_token())
    dead = hash_session_token(generate_session_token())
    store.create_session(alive, "admin", "admin", ttl_seconds=600)
    store.create_session(dead, "admin", "admin", ttl_seconds=-1)
    assert store.delete_expired_sessions() >= 1
    assert store.get_session(alive) is not None

    store.record_auth_event("login_fail", username="admin", client="10.0.0.9", request_id="r1")
    store.record_auth_event("login_ok", username="admin", client="10.0.0.9", request_id="r2")
    events = store.list_auth_events(limit=10)
    assert [event["event"] for event in events[:2]] == ["login_ok", "login_fail"]


# ---------- 脚本 ----------


def test_hash_password_script_outputs_hash(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "argv", [str(_SCRIPT), "roundtrip-pw"])
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(_SCRIPT), run_name="__main__")
    assert exc_info.value.code == 0
    assert verify_password("roundtrip-pw", capsys.readouterr().out.strip()) is True


def test_hash_password_script_rejects_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", [str(_SCRIPT), ""])
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(_SCRIPT), run_name="__main__")
    assert exc_info.value.code == 1
