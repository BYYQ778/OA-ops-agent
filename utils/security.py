"""认证与安全基础（第 5 周）：密码哈希 / 会话令牌 / 登录限速 / 启动安全门禁。

设计要点：
- 纯标准库（hashlib/hmac/secrets），零新依赖，冻结版兼容；
- 密码支持明文（便捷）或 pbkdf2_sha256 哈希（推荐）两种配置形式，恒时比较；
- 会话令牌只存 sha256 哈希（数据库被读也无法重放）；
- 启动门禁：**非回环绑定 +（认证关闭 或 任意用户空/默认密码）→ 拒绝启动**
  （落实「未完成鉴权前不得公开部署」；回环场景仅警告放行）。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from utils.logger import get_logger

_logger = get_logger("oa.security")

PBKDF2_ITERATIONS = 200_000
HASH_PREFIX = "pbkdf2_sha256"
SESSION_COOKIE_NAME = "oa_session"
DEFAULT_SESSION_TTL_HOURS = 12

#: 视为本机的绑定主机/客户端（"testclient" 为进程内测试 harness 的伪主机——
#: ASGI scope 的 client 由服务器侧填充，远端无法伪造）
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})
#: 已知弱默认密码（非回环部署时拒绝）
DEFAULT_PASSWORDS = frozenset({"admin123"})

VALID_ROLES = frozenset({"admin", "viewer"})


def is_loopback_host(host: str) -> bool:
    """是否本机回环主机（127.* / ::1 / localhost / 测试 harness）。"""
    normalized = (host or "").strip().lower()
    if normalized in LOOPBACK_HOSTS:
        return True
    return normalized.startswith("127.")


def env_flag(name: str, default: bool = False) -> bool:
    """读取布尔型环境变量（1/true/yes/on）。"""
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def auth_enabled(config: Any) -> bool:
    """认证开关：OA_AUTH_ENABLED 环境变量 > auth.enabled。"""
    if "OA_AUTH_ENABLED" in os.environ:
        return env_flag("OA_AUTH_ENABLED")
    return bool(config.get("auth.enabled", False))


def bypass_loopback(config: Any) -> bool:
    """回环直通开关（默认 True）：OA_AUTH_BYPASS_LOOPBACK 环境变量 > auth.bypass_loopback。"""
    if "OA_AUTH_BYPASS_LOOPBACK" in os.environ:
        return env_flag("OA_AUTH_BYPASS_LOOPBACK")
    return bool(config.get("auth.bypass_loopback", True))


def session_ttl_seconds(config: Any) -> float:
    """会话有效期（秒），来自 auth.session_ttl_hours（默认 12 小时，下限 60 秒）。"""
    try:
        hours = float(config.get("auth.session_ttl_hours", DEFAULT_SESSION_TTL_HOURS))
    except (TypeError, ValueError):
        hours = float(DEFAULT_SESSION_TTL_HOURS)
    return max(60.0, hours * 3600.0)


# ---------- 密码 ----------


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS, salt: bytes | None = None) -> str:
    """PBKDF2-HMAC-SHA256 哈希，格式：pbkdf2_sha256$iterations$salt_hex$digest_hex。"""
    salt_bytes = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, iterations)
    return f"{HASH_PREFIX}${iterations}${salt_bytes.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验密码：stored 为 pbkdf2 哈希或明文；恒时比较，格式非法一律 False。"""
    stored = stored or ""
    if stored.startswith(HASH_PREFIX + "$"):
        parts = stored.split("$")
        if len(parts) != 4:
            return False
        try:
            iterations = int(parts[1])
            salt = bytes.fromhex(parts[2])
            expected = bytes.fromhex(parts[3])
        except ValueError:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(digest, expected)
    if not stored:
        return False
    return hmac.compare_digest(password.encode("utf-8"), stored.encode("utf-8"))


# ---------- 会话令牌 ----------


def generate_session_token() -> str:
    """生成会话令牌（仅下发给客户端一次；服务端只存哈希）。"""
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """会话令牌的存储形态（sha256 hex）。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------- 用户（config 解析） ----------


@dataclass
class AuthUser:
    """一个可登录用户。"""

    username: str
    password: str = ""
    password_hash: str = ""
    role: str = "viewer"


def _normalize_role(role: Any) -> str:
    value = str(role or "").strip().lower()
    return value if value in VALID_ROLES else "viewer"


def load_users(config: Any) -> List[AuthUser]:
    """解析 config auth 段。

    - 优先 auth.users 列表（多用户：username / password 或 password_hash / role）；
    - 缺省回退旧单用户写法 auth.username + auth.password（角色 admin，向后兼容）。
    """
    raw_users = config.get("auth.users")
    if isinstance(raw_users, list):
        users: List[AuthUser] = []
        for item in raw_users:
            if not isinstance(item, dict):
                continue
            username = str(item.get("username") or "").strip()
            if not username:
                continue
            users.append(
                AuthUser(
                    username=username,
                    password=str(item.get("password") or ""),
                    password_hash=str(item.get("password_hash") or ""),
                    role=_normalize_role(item.get("role")),
                )
            )
        if users:
            return users
    username = str(config.get("auth.username", "admin") or "admin").strip() or "admin"
    password = str(config.get("auth.password", "") or "")
    return [AuthUser(username=username, password=password, role="admin")]


def authenticate(users: List[AuthUser], username: str, password: str) -> Optional[AuthUser]:
    """校验用户名/密码；失败返回 None（空密码账户不可登录）。

    说明：本地单租户工具，未做跨用户恒定时间（避免过度设计）；
    密码比较本身均走恒时比较。
    """
    for user in users:
        if user.username != username:
            continue
        if user.password_hash:
            return user if verify_password(password, user.password_hash) else None
        if user.password:
            return user if verify_password(password, user.password) else None
        return None
    return None


# ---------- 登录限速 ----------


class LoginRateLimiter:
    """按客户端 IP 的失败计数限速（滑动窗口，默认 10 次/分钟）。"""

    def __init__(self, max_attempts: int = 10, window_seconds: float = 60.0) -> None:
        self.max_attempts = max(1, int(max_attempts))
        self.window_seconds = float(window_seconds)
        self._failures: Dict[str, List[float]] = {}

    def _prune(self, client: str, now: float) -> List[float]:
        stamps = [stamp for stamp in self._failures.get(client, []) if now - stamp < self.window_seconds]
        if stamps:
            self._failures[client] = stamps
        else:
            self._failures.pop(client, None)
        return stamps

    def allow(self, client: str, now: Optional[float] = None) -> bool:
        """当前是否允许尝试登录。"""
        moment = time.monotonic() if now is None else now
        return len(self._prune(client, moment)) < self.max_attempts

    def record_failure(self, client: str, now: Optional[float] = None) -> None:
        """记录一次失败尝试。"""
        moment = time.monotonic() if now is None else now
        stamps = self._prune(client, moment)
        stamps.append(moment)
        self._failures[client] = stamps

    def reset(self, client: str) -> None:
        """登录成功后清空该客户端失败计数。"""
        self._failures.pop(client, None)

    def clear(self) -> None:
        self._failures.clear()


# ---------- 启动安全门禁 ----------


@dataclass
class SecurityIssue:
    level: str  # error / warning
    message: str


class SecurityGateError(RuntimeError):
    """非回环绑定存在不可接受的安全配置时抛出。"""

    def __init__(self, issues: List[SecurityIssue]) -> None:
        self.issues = issues
        super().__init__("；".join(issue.message for issue in issues))


def _weak_reason(user: AuthUser) -> str:
    if user.password_hash:
        return ""
    if not user.password:
        return "密码为空"
    if user.password in DEFAULT_PASSWORDS:
        return "使用默认密码"
    return ""


def evaluate_startup_security(config: Any, host: str) -> List[SecurityIssue]:
    """评估「绑定主机 + 认证配置」；返回问题列表（error 阻断 / warning 提示）。"""
    issues: List[SecurityIssue] = []
    users = load_users(config)
    weak_users = [(user.username, _weak_reason(user)) for user in users]
    weak_users = [(name, reason) for name, reason in weak_users if reason]

    if not is_loopback_host(host):
        if not auth_enabled(config):
            issues.append(
                SecurityIssue(
                    "error",
                    f"绑定 {host} 为非回环地址，但认证未启用（auth.enabled=false；未完成鉴权前不得公开部署）",
                )
            )
        else:
            for username, reason in weak_users:
                issues.append(
                    SecurityIssue(
                        "error",
                        f"用户 {username} {reason}；非回环绑定必须先设置强密码"
                        "（.env 的 OA_AUTH_PASSWORD 或 auth.users[].password_hash）",
                    )
                )
    elif auth_enabled(config):
        for username, reason in weak_users:
            issues.append(SecurityIssue("warning", f"用户 {username} {reason}（仅本机回环访问可暂不处理）"))
    return issues


def enforce_startup_security(config: Any, host: str) -> None:
    """启动门禁：存在 error 级问题则抛 SecurityGateError；warning 记日志放行。"""
    issues = evaluate_startup_security(config, host)
    for issue in issues:
        if issue.level == "warning":
            _logger.warning("安全提示：%s", issue.message)
    errors = [issue for issue in issues if issue.level == "error"]
    if errors:
        raise SecurityGateError(errors)
