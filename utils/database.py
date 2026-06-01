"""
数据库模块
---------
SQLite 持久化层，管理巡检记录、告警历史、日志分析记录。

表结构：
- inspection_records: 巡检历史（每次巡检的5项检测结果）
- alert_history: 告警记录（触发条件、通知状态）
- log_analysis_records: 日志分析历史

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
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from utils.config import config
from utils.logger import get_logger

logger = get_logger(__name__)


class Database:
    """SQLite 数据库管理器（线程安全）"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        db_path = config.get("database.sqlite_path", "data/oa_ops.db")
        if not os.path.isabs(db_path):
            base = os.path.dirname(os.path.dirname(__file__))
            db_path = os.path.join(base, db_path)

        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        self._db_path = db_path
        self._conn_local = threading.local()
        logger.info(f"数据库初始化: {db_path}")
        self._init_tables()

        # 注册退出时自动清理
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

    # ========== 工具方法 ==========

    def get_db_stats(self) -> Dict:
        """获取数据库统计信息"""
        tables = ["inspection_records", "alert_history", "log_analysis_records"]
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
        if hasattr(self._conn_local, "conn") and self._conn_local.conn:
            try:
                self._conn_local.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._conn_local.conn.close()
            except Exception:
                pass
            self._conn_local.conn = None
        logger.info("数据库资源已释放")


# 全局单例
db = Database()
