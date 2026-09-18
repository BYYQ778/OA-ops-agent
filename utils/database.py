"""
数据库模块
---------
SQLite 持久化层，管理巡检记录、告警历史、日志分析记录。

表结构：
- inspection_records: 巡检历史（每次巡检的5项检测结果）
- alert_history: 告警记录（触发条件、通知状态）
- log_analysis_records: 日志分析历史
- incidents: 根因诊断报告（完整 JSON + 摘要列，第 4 周）
- sessions / auth_events: 认证会话与登录审计（第 5 周）
- conversations: 对话会话（标题、更新时间、消息数）
- conversation_messages: 对话消息（role/content/顺序）

使用方式：
    from utils.database import db
    db.save_inspection(check_type, target, result, status)
    records = db.get_inspection_history(days=7)
"""

import os
import atexit
import sqlite3
import json
import threading
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from utils.config import config, get_app_root
from utils.logger import get_logger

logger = get_logger(__name__)


class Database:
    """SQLite 数据库管理器（线程安全）"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, db_path: os.PathLike[str] | str | None = None):
        if db_path is not None:
            instance = super().__new__(cls)
            instance._initialized = False
            return instance
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, db_path: os.PathLike[str] | str | None = None):
        if self._initialized:
            return
        self._initialized = True
        manages_process_lifecycle = db_path is None

        if db_path is None:
            configured_path = Path(config.get("database.sqlite_path", "data/oa_ops.db"))
            data_dir = os.environ.get("OA_DATA_DIR")
            if data_dir:
                db_path = Path(data_dir) / configured_path.name
            elif configured_path.is_absolute():
                db_path = configured_path
            else:
                db_path = Path(get_app_root()) / configured_path
        db_path = Path(db_path).resolve()

        db_path.parent.mkdir(parents=True, exist_ok=True)

        self._db_path = str(db_path)
        self._conn_local = threading.local()
        logger.info(f"数据库初始化: {db_path}")
        self._init_tables()

        # 默认全局数据库随进程退出清理；显式路径实例由调用方管理生命周期。
        if manages_process_lifecycle:
            atexit.register(self.close_all)

    @property
    def _conn(self) -> sqlite3.Connection:
        """获取当前线程的数据库连接（自动创建）"""
        if not hasattr(self._conn_local, "conn") or self._conn_local.conn is None:
            self._conn_local.conn = sqlite3.connect(self._db_path)
            self._conn_local.conn.row_factory = sqlite3.Row
            self._conn_local.conn.execute("PRAGMA journal_mode=WAL")
            self._conn_local.conn.execute("PRAGMA foreign_keys=ON")
        return self._conn_local.conn

    def _init_tables(self):
        """创建数据库表"""
        with self._lock:
            conn = self._conn
            conn.executescript("""
                -- 巡检记录表
                CREATE TABLE IF NOT EXISTS inspection_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    check_time TEXT NOT NULL,          -- 巡检时间 ISO8601
                    check_type TEXT NOT NULL,          -- ports/nginx/oa/disk/memory
                    check_type_cn TEXT NOT NULL,       -- 端口检测/Nginx检测/OA服务/磁盘/内存
                    target TEXT DEFAULT '',            -- 检测目标（如端口号、挂载点）
                    result TEXT NOT NULL,              -- 完整检测结果文本
                    status TEXT NOT NULL DEFAULT 'normal',  -- normal/warning/error
                    is_simulated INTEGER DEFAULT 1,    -- 0=真实检测, 1=模拟数据
                    extra_json TEXT DEFAULT '{}',      -- 扩展数据（JSON）
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );

                -- 巡检记录索引
                CREATE INDEX IF NOT EXISTS idx_inspection_time
                    ON inspection_records(check_time);
                CREATE INDEX IF NOT EXISTS idx_inspection_type
                    ON inspection_records(check_type);
                CREATE INDEX IF NOT EXISTS idx_inspection_status
                    ON inspection_records(status);

                -- 告警记录表
                CREATE TABLE IF NOT EXISTS alert_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_time TEXT NOT NULL,
                    alert_type TEXT NOT NULL,          -- inspection/log_analysis
                    severity TEXT NOT NULL,            -- info/warning/critical
                    title TEXT NOT NULL,               -- 告警标题
                    detail TEXT NOT NULL,              -- 告警详情
                    notified INTEGER DEFAULT 0,       -- 0=未通知, 1=已通知
                    notify_channel TEXT DEFAULT '',    -- email/dingtalk/wecom
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );

                CREATE INDEX IF NOT EXISTS idx_alert_time
                    ON alert_history(alert_time);
                CREATE INDEX IF NOT EXISTS idx_alert_notified
                    ON alert_history(notified);

                -- 根因诊断记录表（第 4 周）
                CREATE TABLE IF NOT EXISTS incidents (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    service TEXT DEFAULT '',
                    host TEXT DEFAULT '',
                    status TEXT NOT NULL,
                    root_cause TEXT DEFAULT '',
                    root_title TEXT DEFAULT '',
                    confidence REAL DEFAULT 0,
                    source TEXT DEFAULT '',
                    report_json TEXT NOT NULL,
                    events_json TEXT DEFAULT '[]',
                    created_db TEXT DEFAULT (datetime('now','localtime'))
                );

                CREATE INDEX IF NOT EXISTS idx_incidents_created
                    ON incidents(created_at);

                -- 日志分析记录表
                CREATE TABLE IF NOT EXISTS log_analysis_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    analysis_time TEXT NOT NULL,
                    log_source TEXT DEFAULT '',        -- 文件路径 或 "手动粘贴"
                    log_size INTEGER DEFAULT 0,        -- 日志大小（字符数）
                    faults_found INTEGER DEFAULT 0,    -- 发现的故障数
                    severe_count INTEGER DEFAULT 0,
                    high_count INTEGER DEFAULT 0,
                    medium_count INTEGER DEFAULT 0,
                    report TEXT NOT NULL,              -- 完整分析报告
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );

                CREATE INDEX IF NOT EXISTS idx_analysis_time
                    ON log_analysis_records(analysis_time);
                -- 对话会话表
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,               -- 对话 ID（uuid4 字符串）
                    title TEXT NOT NULL DEFAULT '新对话',
                    created_at TEXT DEFAULT (datetime('now','localtime')),
                    updated_at TEXT DEFAULT (datetime('now','localtime')),
                    message_count INTEGER DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_conversations_updated
                    ON conversations(updated_at DESC);

                -- 对话消息表
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,                -- user / assistant
                    content TEXT NOT NULL,
                    seq INTEGER NOT NULL DEFAULT 0,    -- 消息顺序（从0开始）
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );

                CREATE INDEX IF NOT EXISTS idx_msg_conversation
                    ON conversation_messages(conversation_id, seq);

                -- 会话表（第 5 周）：只存 token 的 sha256 哈希（库泄露也无法重放）
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    role TEXT NOT NULL,
                    client TEXT DEFAULT '',
                    created_at REAL NOT NULL,          -- epoch 秒
                    expires_at REAL NOT NULL           -- epoch 秒
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_expires
                    ON sessions(expires_at);

                -- 认证事件审计表（第 5 周）
                CREATE TABLE IF NOT EXISTS auth_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    event TEXT NOT NULL,               -- login_ok / login_fail / logout / login_rate_limited
                    username TEXT DEFAULT '',
                    client TEXT DEFAULT '',
                    request_id TEXT DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_auth_events_ts
                    ON auth_events(ts);
            """)
            conn.commit()
            logger.info("数据库表初始化完成")

    # ========== 巡检记录 ==========

    def save_inspection(
        self,
        check_type: str,
        check_type_cn: str,
        target: str,
        result: str,
        status: str = "normal",
        is_simulated: bool = True,
        extra: dict = None,
    ) -> int:
        """
        保存一条巡检记录。

        Args:
            check_type: 检测类型 (ports/nginx/oa/disk/memory)
            check_type_cn: 中文名称
            target: 检测目标
            result: 检测结果文本
            status: normal/warning/error
            is_simulated: 是否模拟数据
            extra: 扩展数据

        Returns:
            新纪录的 ID
        """
        check_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO inspection_records
                   (check_time, check_type, check_type_cn, target, result, status, is_simulated, extra_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    check_time,
                    check_type,
                    check_type_cn,
                    target,
                    result,
                    status,
                    1 if is_simulated else 0,
                    json.dumps(extra or {}, ensure_ascii=False),
                )
            )
            self._conn.commit()
            return cursor.lastrowid

    def save_inspection_batch(self, records: List[Dict]) -> int:
        """
        批量保存巡检记录（一次完整巡检的5项结果）。

        Args:
            records: [{"check_type": "ports", "check_type_cn": "端口检测", ...}, ...]

        Returns:
            保存的记录数
        """
        check_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        count = 0
        with self._lock:
            for r in records:
                self._conn.execute(
                    """INSERT INTO inspection_records
                       (check_time, check_type, check_type_cn, target, result, status, is_simulated, extra_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        check_time,
                        r.get("check_type", ""),
                        r.get("check_type_cn", ""),
                        r.get("target", ""),
                        r.get("result", ""),
                        r.get("status", "normal"),
                        r.get("is_simulated", 1),
                        json.dumps(r.get("extra", {}), ensure_ascii=False),
                    )
                )
                count += 1
            self._conn.commit()
        logger.info(f"批量保存 {count} 条巡检记录")
        return count

    def get_inspection_history(
        self,
        days: int = 7,
        check_type: str = None,
        status: str = None,
        limit: int = 100,
    ) -> List[Dict]:
        """
        查询巡检历史记录。

        Args:
            days: 最近N天
            check_type: 过滤检测类型
            status: 过滤状态
            limit: 最大返回数

        Returns:
            记录列表
        """
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        sql = "SELECT * FROM inspection_records WHERE check_time >= ?"
        params = [since]

        if check_type:
            sql += " AND check_type = ?"
            params.append(check_type)
        if status:
            sql += " AND status = ?"
            params.append(status)

        sql += " ORDER BY check_time DESC LIMIT ?"
        params.append(limit)

        cursor = self._conn.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]

    def get_inspection_summary(self, days: int = 7) -> Dict:
        """
        获取巡检汇总统计。

        Returns:
            {total, normal, warning, error, by_type: {...}}
        """
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        cursor = self._conn.execute(
            """SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status='normal' THEN 1 ELSE 0 END) as normal_count,
                SUM(CASE WHEN status='warning' THEN 1 ELSE 0 END) as warning_count,
                SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) as error_count
            FROM inspection_records WHERE check_time >= ?""",
            (since,)
        )
        row = cursor.fetchone()

        # 按类型统计
        cursor2 = self._conn.execute(
            """SELECT check_type_cn, COUNT(*) as cnt,
                SUM(CASE WHEN status!='normal' THEN 1 ELSE 0 END) as abnormal
            FROM inspection_records WHERE check_time >= ?
            GROUP BY check_type_cn""",
            (since,)
        )
        by_type = {r["check_type_cn"]: {"total": r["cnt"], "abnormal": r["abnormal"]}
                   for r in cursor2.fetchall()}

        return {
            "total": row["total"] or 0,
            "normal": row["normal_count"] or 0,
            "warning": row["warning_count"] or 0,
            "error": row["error_count"] or 0,
            "by_type": by_type,
        }

    # ========== 告警记录 ==========

    def save_alert(
        self,
        alert_type: str,
        severity: str,
        title: str,
        detail: str,
    ) -> int:
        """
        保存告警记录。

        Args:
            alert_type: inspection / log_analysis
            severity: info / warning / critical
            title: 告警标题
            detail: 告警详情
        """
        alert_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO alert_history
                   (alert_time, alert_type, severity, title, detail)
                   VALUES (?, ?, ?, ?, ?)""",
                (alert_time, alert_type, severity, title, detail)
            )
            self._conn.commit()
            return cursor.lastrowid

    def get_pending_alerts(self) -> List[Dict]:
        """获取未通知的告警"""
        cursor = self._conn.execute(
            "SELECT * FROM alert_history WHERE notified=0 ORDER BY alert_time DESC"
        )
        return [dict(row) for row in cursor.fetchall()]

    def mark_alert_notified(self, alert_id: int, channel: str = "email"):
        """标记告警已通知"""
        with self._lock:
            self._conn.execute(
                "UPDATE alert_history SET notified=1, notify_channel=? WHERE id=?",
                (channel, alert_id)
            )
            self._conn.commit()

    def get_alert_history(self, days: int = 7, limit: int = 50) -> List[Dict]:
        """查询告警历史"""
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        cursor = self._conn.execute(
            "SELECT * FROM alert_history WHERE alert_time >= ? ORDER BY alert_time DESC LIMIT ?",
            (since, limit)
        )
        return [dict(row) for row in cursor.fetchall()]

    # ========== 日志分析记录 ==========

    def save_log_analysis(
        self,
        log_source: str,
        log_size: int,
        faults_found: int,
        severe_count: int,
        high_count: int,
        medium_count: int,
        report: str,
    ) -> int:
        """保存日志分析记录"""
        analysis_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO log_analysis_records
                   (analysis_time, log_source, log_size, faults_found,
                    severe_count, high_count, medium_count, report)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (analysis_time, log_source, log_size, faults_found,
                 severe_count, high_count, medium_count, report)
            )
            self._conn.commit()
            return cursor.lastrowid

    def get_log_analysis_history(self, days: int = 7, limit: int = 20) -> List[Dict]:
        """查询日志分析历史"""
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        cursor = self._conn.execute(
            """SELECT id, analysis_time, log_source, log_size, faults_found,
                      severe_count, high_count, medium_count, created_at
               FROM log_analysis_records
               WHERE analysis_time >= ? ORDER BY analysis_time DESC LIMIT ?""",
            (since, limit)
        )
        return [dict(row) for row in cursor.fetchall()]

    # ========== 根因诊断记录（第 4 周）==========

    def save_incident(self, report: Dict, service: str = "", host: str = "", source: str = "") -> str:
        """保存根因诊断报告。

        Args:
            report: DiagnosisReport.model_dump(mode="json") 产生的字典
            service/host: 关联服务与主机（可选，用于列表展示）
            source: 入口来源（api / ui / eval）

        Returns:
            诊断编号 incident_id（同一编号重复保存为覆盖）
        """
        incident_id = str(report["incident_id"])
        root = report.get("root_cause") or {}
        created_at = str(report.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO incidents
                   (id, created_at, service, host, status, root_cause, root_title,
                    confidence, source, report_json, events_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    incident_id,
                    created_at,
                    service,
                    host,
                    str(report.get("status") or "uncertain"),
                    str(root.get("cause_id") or ""),
                    str(root.get("title") or ""),
                    float(report.get("confidence") or 0.0),
                    source,
                    json.dumps(report, ensure_ascii=False),
                    json.dumps(report.get("events") or [], ensure_ascii=False),
                ),
            )
            self._conn.commit()
        return incident_id

    def get_incident(self, incident_id: str) -> Optional[Dict]:
        """按编号取诊断报告（report/events 为解析后的 JSON；不存在返回 None）。"""
        cursor = self._conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        data = dict(row)
        try:
            data["report"] = json.loads(data.pop("report_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            data["report"] = {}
        try:
            data["events"] = json.loads(data.pop("events_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            data["events"] = []
        return data

    def list_incidents(self, limit: int = 50) -> List[Dict]:
        """诊断记录摘要列表（按生成时间倒序）。"""
        cursor = self._conn.execute(
            """SELECT id, created_at, service, host, status, root_cause, root_title, confidence
               FROM incidents ORDER BY created_at DESC, rowid DESC LIMIT ?""",
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    # ========== 认证会话与审计（第 5 周）==========

    def create_session(self, token_hash: str, username: str, role: str, ttl_seconds: float, client: str = "") -> None:
        """写入会话记录（同一 token_hash 覆盖；expires_at 为 epoch 秒）。"""
        now = time.time()
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO sessions
                   (token_hash, username, role, client, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (token_hash, username, role, client, now, now + float(ttl_seconds)),
            )
            self._conn.commit()

    def get_session(self, token_hash: str, now: Optional[float] = None) -> Optional[Dict]:
        """取会话；过期自动删除并返回 None。"""
        moment = time.time() if now is None else now
        cursor = self._conn.execute("SELECT * FROM sessions WHERE token_hash = ?", (token_hash,))
        row = cursor.fetchone()
        if row is None:
            return None
        record = dict(row)
        if float(record.get("expires_at") or 0) <= moment:
            self.delete_session(token_hash)
            return None
        return record

    def delete_session(self, token_hash: str) -> bool:
        """删除会话；返回是否命中。"""
        with self._lock:
            cursor = self._conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            self._conn.commit()
        return cursor.rowcount > 0

    def delete_expired_sessions(self, now: Optional[float] = None) -> int:
        """清理过期会话，返回清理条数。"""
        moment = time.time() if now is None else now
        with self._lock:
            cursor = self._conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (moment,))
            self._conn.commit()
        return int(cursor.rowcount or 0)

    def record_auth_event(self, event: str, username: str = "", client: str = "", request_id: str = "") -> None:
        """记录认证事件（登录成功/失败/登出等，审计用）。"""
        with self._lock:
            self._conn.execute(
                "INSERT INTO auth_events (ts, event, username, client, request_id) VALUES (?, ?, ?, ?, ?)",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), str(event), username, client, request_id),
            )
            self._conn.commit()

    def list_auth_events(self, limit: int = 50) -> List[Dict]:
        """最近的认证事件（倒序）。"""
        cursor = self._conn.execute(
            "SELECT id, ts, event, username, client, request_id FROM auth_events ORDER BY id DESC LIMIT ?",
            (max(1, int(limit)),),
        )
        return [dict(row) for row in cursor.fetchall()]

    # ========== 对话历史（持久化）==========

    def create_conversation(self, title: str = "新对话", conversation_id: str = None) -> str:
        """新建对话会话，返回对话 ID。conversation_id 为空时自动生成 uuid4。"""
        import uuid
        conv_id = conversation_id or uuid.uuid4().hex
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            self._conn.execute(
                "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (conv_id, title, now, now)
            )
            self._conn.commit()
        return conv_id

    def save_message(self, conversation_id: str, role: str, content: str) -> int:
        """保存一条对话消息；同时更新会话的 updated_at 与 message_count。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), -1) + 1 AS next_seq "
                "FROM conversation_messages WHERE conversation_id = ?",
                (conversation_id,)
            ).fetchone()
            seq = int(row["next_seq"]) if row else 0
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor = self._conn.execute(
                "INSERT INTO conversation_messages (conversation_id, role, content, seq, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (conversation_id, role, content, seq, now)
            )
            self._conn.execute(
                "UPDATE conversations SET updated_at = ?, message_count = message_count + 1 "
                "WHERE id = ?",
                (now, conversation_id)
            )
            self._conn.commit()
            return cursor.lastrowid

    def get_conversation_messages(self, conversation_id: str, limit: int = 200) -> List[Dict]:
        """获取某对话的全部消息（按 seq 升序）。"""
        cursor = self._conn.execute(
            "SELECT id, role, content, seq, created_at FROM conversation_messages "
            "WHERE conversation_id = ? ORDER BY seq ASC LIMIT ?",
            (conversation_id, limit)
        )
        return [dict(row) for row in cursor.fetchall()]

    def list_conversations(self, limit: int = 50) -> List[Dict]:
        """对话列表：按最近更新倒序，附带首条用户消息预览。"""
        cursor = self._conn.execute(
            """SELECT c.id, c.title, c.created_at, c.updated_at, c.message_count,
                      (SELECT m.content FROM conversation_messages m
                       WHERE m.conversation_id = c.id AND m.role = 'user'
                       ORDER BY m.seq ASC LIMIT 1) AS preview
               FROM conversations c
               ORDER BY c.updated_at DESC LIMIT ?""",
            (limit,)
        )
        return [dict(row) for row in cursor.fetchall()]

    def delete_conversation(self, conversation_id: str) -> bool:
        """删除对话及其全部消息，返回是否删除成功。"""
        with self._lock:
            self._conn.execute(
                "DELETE FROM conversation_messages WHERE conversation_id = ?",
                (conversation_id,)
            )
            cursor = self._conn.execute(
                "DELETE FROM conversations WHERE id = ?", (conversation_id,)
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def rename_conversation(self, conversation_id: str, title: str) -> bool:
        """重命名对话标题。"""
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE conversations SET title = ? WHERE id = ?",
                (title, conversation_id)
            )
            self._conn.commit()
            return cursor.rowcount > 0
    # ========== 工具方法 ==========

    def get_db_stats(self) -> Dict:
        """获取数据库统计信息"""
        tables = ["inspection_records", "alert_history", "log_analysis_records",
                  "conversations", "conversation_messages"]
        stats = {}
        for table in tables:
            cursor = self._conn.execute(f"SELECT COUNT(*) as cnt FROM {table}")
            stats[table] = cursor.fetchone()["cnt"]
        stats["db_path"] = self._db_path
        return stats

    def vacuum(self):
        """压缩数据库文件"""
        with self._lock:
            self._conn.execute("VACUUM")
            logger.info("数据库 VACUUM 完成")

    def close(self):
        """关闭当前线程的数据库连接"""
        if hasattr(self._conn_local, "conn") and self._conn_local.conn:
            try:
                self._conn_local.conn.close()
            except Exception:
                pass
            self._conn_local.conn = None
            logger.info("数据库连接已关闭")

    def close_all(self):
        """强制关闭所有连接并 checkpoint WAL（进程退出时调用）"""
        had_connection = hasattr(self._conn_local, "conn") and self._conn_local.conn is not None
        if had_connection:
            try:
                self._conn_local.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._conn_local.conn.close()
            except Exception:
                pass
            self._conn_local.conn = None


# 全局单例
db = Database()
