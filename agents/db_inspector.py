"""
数据库健康巡检模块
-----------------
检测 MySQL 和 Redis 的运行状态，覆盖运维日常关注的核心指标：
- MySQL: 连接检测、慢查询统计、连接数/最大连接、InnoDB 缓冲池命中率、主从延迟
- Redis: 内存使用、键命中率、连接数、持久化状态、过期键数量

支持两种模式：
- 本地直连（本机数据库）
- SSH 远程（复用 inspection_real.py 的 SSH 连接，在远程执行 mysql/redis-cli 命令）

使用方式:
    from agents.db_inspector import DBCheckAgent
    agent = DBCheckAgent()
    print(agent.check_mysql("localhost", 3306, "root", "password"))
"""

import os
import sys
from datetime import datetime
from typing import Optional  # noqa: F401 保留供类型标注使用

from langchain.tools import tool

from utils.logger import get_logger
from utils.config import config

logger = get_logger(__name__)

IS_WINDOWS = sys.platform == "win32"


def _exec_sql(host: str, port: int, user: str, password: str, sql: str, database: str = "") -> str:
    """
    执行 MySQL 查询（通过 mysql 命令行客户端）。
    如果目标不在本机，调用方应通过 SSH 执行。
    """
    mysql_cmd = "mysql" if not IS_WINDOWS else "mysql.exe"

    cmd_parts = [
        mysql_cmd,
        f"-h{host}",
        f"-P{port}",
        f"-u{user}",
        f"-p{password}",
        "-N",       # 跳过列名
        "-B",       # 批量模式（Tab 分隔）
        "-e", sql,
    ]
    if database:
        cmd_parts.insert(-2, database)

    cmd = " ".join(cmd_parts)
    # 隐藏密码的命令用于日志
    safe_cmd = cmd.replace(password, "***")

    import subprocess
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=15,
            encoding="gbk" if IS_WINDOWS else "utf-8",
            errors="ignore",
        )
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            err_msg = result.stderr.strip()
            if "Access denied" in err_msg:
                return "__ERROR__: 数据库认证失败，请检查用户名和密码"
            if "Can't connect" in err_msg:
                return f"__ERROR__: 无法连接到 {host}:{port}，请检查数据库服务状态和防火墙"
            return f"__ERROR__: {err_msg[:200]}"
    except subprocess.TimeoutExpired:
        return "__ERROR__: 查询超时"
    except FileNotFoundError:
        return "__ERROR__: mysql 客户端未安装，请先安装 MySQL Client"


def _exec_redis(host: str, port: int, password: str, command: str) -> str:
    """通过 redis-cli 执行 Redis 命令"""
    redis_cmd = "redis-cli" if not IS_WINDOWS else "redis-cli.exe"

    cmd_parts = [redis_cmd, "-h", host, "-p", str(port)]
    if password:
        cmd_parts.extend(["-a", password])
    cmd_parts.extend(command.split())

    cmd = " ".join(cmd_parts)

    import subprocess
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=10,
            encoding="gbk" if IS_WINDOWS else "utf-8",
            errors="ignore",
        )
        output = result.stdout.strip() or result.stderr.strip()
        if "NOAUTH" in output or "ERR AUTH" in output:
            return "__ERROR__: Redis 认证失败，请检查密码"
        if "Connection refused" in output:
            return f"__ERROR__: 无法连接到 {host}:{port}"
        return output
    except subprocess.TimeoutExpired:
        return "__ERROR__: Redis 查询超时"
    except FileNotFoundError:
        return "__ERROR__: redis-cli 客户端未安装"


# ========== MySQL 检测工具 ==========

@tool
def check_mysql_status(config_text: str) -> str:
    """
    检测 MySQL 数据库健康状态。

    Args:
        config_text: 数据库连接信息，格式:
            host=127.0.0.1 port=3306 user=root password=yourpass

    Returns:
        MySQL 健康报告：连接状态、慢查询数、连接使用率、InnoDB 缓冲池命中率
    """
    # 解析参数
    params = {}
    for part in config_text.replace("\n", " ").split():
        if "=" in part:
            k, v = part.split("=", 1)
            params[k.strip()] = v.strip()

    host = params.get("host", "127.0.0.1")
    port = int(params.get("port", 3306))
    user = params.get("user", "root")
    password = params.get("password", "")

    if not password:
        # 尝试从环境变量读取
        password = os.environ.get("OA_MYSQL_PASSWORD", "")

    if not password:
        return (
            "[提示] 未提供数据库密码。请按以下格式输入:\n"
            "  host=127.0.0.1 port=3306 user=root password=你的密码\n"
            "或在 .env 文件中设置 OA_MYSQL_PASSWORD=你的密码"
        )

    lines = [
        "=" * 55,
        f"  MySQL 数据库健康巡检报告",
        "=" * 55,
        f"目标: {host}:{port}",
        f"用户: {user}",
        f"检测时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 55,
        "",
    ]

    # --- 基础连接检测 ---
    version_sql = "SELECT VERSION()"
    result = _exec_sql(host, port, user, password, version_sql)
    if "__ERROR__" in result:
        lines.append(f"❌ 连接失败: {result.replace('__ERROR__: ', '')}")
        lines.append("")
        lines.append("💡 排查建议:")
        lines.append("  1. 确认 MySQL 服务已启动: systemctl status mysql/mysqld")
        lines.append("  2. 检查端口是否监听: netstat -tlnp | grep 3306")
        lines.append("  3. 检查防火墙规则是否放行 3306 端口")
        lines.append("  4. 确认用户有远程连接权限: SELECT user,host FROM mysql.user")
        lines.append("=" * 55)
        return "\n".join(lines)

    version = result.strip()
    lines.append(f"✅ 连接成功 — MySQL 版本: {version}")
    lines.append("")

    # --- 连接数统计 ---
    conn_sql = (
        "SELECT "
        "  (SELECT COUNT(*) FROM information_schema.PROCESSLIST) AS current_conn, "
        "  (SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME='Threads_connected') AS threads, "
        "  @@max_connections AS max_conn"
    )
    result = _exec_sql(host, port, user, password, conn_sql)
    if not result.startswith("__ERROR__"):
        parts = result.split("\t")
        if len(parts) >= 3:
            current = parts[0]
            max_conn = parts[-1]
            try:
                conn_pct = round(int(current) / int(max_conn) * 100, 1) if max_conn != "0" else 0
            except ValueError:
                conn_pct = 0
            if conn_pct >= 80:
                lines.append(f"⚠️ 连接使用率: {current}/{max_conn} ({conn_pct}%) — 接近上限！")
            else:
                lines.append(f"✅ 连接使用率: {current}/{max_conn} ({conn_pct}%)")

    # --- 慢查询统计 ---
    slow_sql = (
        "SELECT VARIABLE_VALUE FROM performance_schema.global_variables "
        "WHERE VARIABLE_NAME='long_query_time'"
    )
    try:
        threshold_result = _exec_sql(host, port, user, password, slow_sql)
        if threshold_result and not threshold_result.startswith("__ERROR__"):
            # 参数化查询慢查询
            slow_sql_count = (
                "SELECT COUNT(*) FROM mysql.slow_log "
                "WHERE start_time > DATE_SUB(NOW(), INTERVAL 24 HOUR)"
            )
            count_result = _exec_sql(host, port, user, password, slow_sql_count)
            if count_result and not count_result.startswith("__ERROR__") and "doesn't exist" not in count_result:
                slow_count = count_result.strip()
                if slow_count.isdigit() and int(slow_count) > 0:
                    lines.append(f"🟡 近24小时慢查询: {slow_count} 条 (阈值: {threshold_result.strip()}秒)")
                else:
                    lines.append(f"✅ 近24小时慢查询: 0 条")
            else:
                lines.append("ℹ️ 慢查询日志未开启 (slow_query_log = OFF)")
    except Exception:
        pass

    # --- InnoDB 缓冲池命中率 ---
    innodb_sql = (
        "SELECT "
        "  (SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME='Innodb_buffer_pool_read_requests') AS reads, "
        "  (SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME='Innodb_buffer_pool_reads') AS physical_reads"
    )
    try:
        innodb_result = _exec_sql(host, port, user, password, innodb_sql)
        if innodb_result and not innodb_result.startswith("__ERROR__"):
            parts = innodb_result.split("\t")
            if len(parts) == 2:
                reads = int(parts[0]) if parts[0].isdigit() else 0
                physical = int(parts[1]) if parts[1].isdigit() else 0
                if reads > 0:
                    hit_rate = round((1 - physical / reads) * 100, 1)
                    if hit_rate >= 99:
                        lines.append(f"✅ InnoDB 缓冲池命中率: {hit_rate}%")
                    elif hit_rate >= 95:
                        lines.append(f"🟡 InnoDB 缓冲池命中率: {hit_rate}% — 偏低，考虑增大 innodb_buffer_pool_size")
                    else:
                        lines.append(f"🔴 InnoDB 缓冲池命中率: {hit_rate}% — 严重偏低！")
    except Exception:
        pass

    # --- 主从延迟 ---
    slave_sql = "SHOW SLAVE STATUS\\G"
    try:
        slave_result = _exec_sql(host, port, user, password, slave_sql)
        if slave_result and "Seconds_Behind_Master" in slave_result:
            for line in slave_result.split("\n"):
                if "Seconds_Behind_Master:" in line:
                    delay = line.split(":")[-1].strip()
                    if delay == "NULL":
                        lines.append("ℹ️ 主从复制: 未配置或SLAVE未运行")
                    elif delay == "0":
                        lines.append("✅ 主从复制延迟: 0秒")
                    else:
                        lines.append(f"🟡 主从复制延迟: {delay}秒")
                    break
    except Exception:
        pass

    lines.append("")
    lines.append("─" * 30 + " 改进建议 " + "─" * 30)
    lines.append("• 定期执行 OPTIMIZE TABLE 清理碎片")
    lines.append("• 监控慢查询日志，优化执行计划")
    lines.append("• 保持 innodb_buffer_pool_size 为物理内存的 50-70%")
    lines.append("• 定期备份: mysqldump --single-transaction --all-databases")
    lines.append("=" * 55)

    return "\n".join(lines)


@tool
def show_mysql_slow_queries(config_text: str) -> str:
    """
    显示 MySQL 最近的慢查询记录。

    Args:
        config_text: host=127.0.0.1 port=3306 user=root password=pass [limit=10]

    Returns:
        慢查询列表
    """
    params = {}
    for part in config_text.replace("\n", " ").split():
        if "=" in part:
            k, v = part.split("=", 1)
            params[k.strip()] = v.strip()

    host = params.get("host", "127.0.0.1")
    port = int(params.get("port", 3306))
    user = params.get("user", "root")
    password = params.get("password", "")
    limit = int(params.get("limit", 10))

    if not password:
        password = os.environ.get("OA_MYSQL_PASSWORD", "")

    if not password:
        return "[提示] 请提供数据库密码"

    sql = (
        f"SELECT start_time, lock_time, rows_sent, rows_examined, "
        f"LEFT(sql_text, 200) FROM mysql.slow_log "
        f"ORDER BY start_time DESC LIMIT {limit}"
    )
    result = _exec_sql(host, port, user, password, sql)

    if "__ERROR__" in result:
        return f"查询失败: {result.replace('__ERROR__: ', '')}"
    if not result:
        return "✅ 近24小时无慢查询记录"

    lines = [
        "=" * 55,
        f"  MySQL 慢查询记录 (最近 {limit} 条)",
        "=" * 55,
        "时间\t锁等待\t发送行\t扫描行\tSQL摘要",
        "─" * 55,
    ]
    for row in result.split("\n"):
        lines.append(row.replace("\t", " | "))
    lines.append("=" * 55)
    return "\n".join(lines)


# ========== Redis 检测工具 ==========

@tool
def check_redis_status(config_text: str) -> str:
    """
    检测 Redis 服务健康状态。

    Args:
        config_text: host=127.0.0.1 port=6379 password=yourpass（无密码可不填）

    Returns:
        Redis 健康报告：内存使用、命中率、连接数、键数量、持久化状态
    """
    params = {}
    for part in config_text.replace("\n", " ").split():
        if "=" in part:
            k, v = part.split("=", 1)
            params[k.strip()] = v.strip()

    host = params.get("host", "127.0.0.1")
    port = int(params.get("port", 6379))
    password = params.get("password", "")

    lines = [
        "=" * 55,
        f"  Redis 健康巡检报告",
        "=" * 55,
        f"目标: {host}:{port}",
        f"检测时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 55,
        "",
    ]

    # --- PING 检测 ---
    ping_result = _exec_redis(host, port, password, "PING")
    if "__ERROR__" in ping_result or ping_result.strip() != "PONG":
        lines.append(f"❌ 连接失败: {ping_result}")
        lines.append("")
        lines.append("💡 排查建议:")
        lines.append("  1. 确认 Redis 服务已启动: systemctl status redis")
        lines.append("  2. 检查端口监听: netstat -tlnp | grep 6379")
        lines.append("  3. 检查 bind 配置: 确认 redis.conf 中 bind 地址")
        lines.append("=" * 55)
        return "\n".join(lines)

    lines.append("✅ 连接成功 — PING 响应正常")
    lines.append("")

    # --- INFO 统计 ---
    info_output = _exec_redis(host, port, password, "INFO")
    if info_output.startswith("__ERROR__"):
        lines.append(f"❌ 获取 INFO 失败: {info_output}")
        lines.append("=" * 55)
        return "\n".join(lines)

    # 解析 INFO 输出
    info_dict = {}
    for line in info_output.split("\n"):
        line = line.strip()
        if line and not line.startswith("#") and ":" in line:
            k, v = line.split(":", 1)
            info_dict[k] = v

    # --- 内存使用 ---
    used_memory_human = info_dict.get("used_memory_human", "未知")
    max_memory = info_dict.get("maxmemory_human", "未限制")
    mem_frag = info_dict.get("mem_fragmentation_ratio", "未知")
    lines.append(f"📊 内存使用: {used_memory_human} / {max_memory}")
    if mem_frag and mem_frag != "未知":
        frag_val = float(mem_frag)
        if frag_val > 2:
            lines.append(f"   ⚠️ 内存碎片率: {mem_frag} (> 2，建议执行 MEMORY PURGE)")

    # --- 命中率 ---
    keyspace_hits = int(info_dict.get("keyspace_hits", 0))
    keyspace_misses = int(info_dict.get("keyspace_misses", 0))
    total_ops = keyspace_hits + keyspace_misses
    if total_ops > 0:
        hit_rate = round(keyspace_hits / total_ops * 100, 1)
        if hit_rate >= 95:
            lines.append(f"✅ 缓存命中率: {hit_rate}%")
        elif hit_rate >= 80:
            lines.append(f"🟡 缓存命中率: {hit_rate}% — 偏低")
        else:
            lines.append(f"🔴 缓存命中率: {hit_rate}% — 严重偏低，考虑增加内存或优化过期策略")
    else:
        lines.append("ℹ️ 无键操作记录")

    # --- 连接数 ---
    connected = info_dict.get("connected_clients", "0")
    blocked = info_dict.get("blocked_clients", "0")
    lines.append(f"🔗 连接数: {connected} 客户端 (阻塞: {blocked})")

    # --- 键统计 ---
    total_keys = "0"
    for kline in info_output.split("\n"):
        if kline.startswith("db"):
            parts = kline.split(":")[1].split(",")
            for p in parts:
                if "keys=" in p:
                    total_keys = str(int(total_keys) + int(p.split("=")[1]))
    lines.append(f"🗂️ 键总数: {total_keys} 个")

    # --- 过期键 ---
    expired_keys = info_dict.get("expired_keys", "0")
    evicted_keys = info_dict.get("evicted_keys", "0")
    if int(evicted_keys) > 0:
        lines.append(f"⚠️ 因内存不足驱逐的键: {evicted_keys} 个 (考虑增大 maxmemory)")
    lines.append(f"📅 已过期键: {expired_keys} 个")

    # --- 持久化状态 ---
    rdb_last_save = info_dict.get("rdb_last_save_time", "0")
    rdb_changes = info_dict.get("rdb_changes_since_last_save", "0")
    aof_enabled = info_dict.get("aof_enabled", "0")

    if rdb_last_save != "0":
        last_save_time = datetime.fromtimestamp(int(rdb_last_save)).strftime('%Y-%m-%d %H:%M:%S')
        lines.append(f"💾 RDB 最后保存: {last_save_time} (变更: {rdb_changes})")
    else:
        lines.append("⚠️ RDB 持久化未开启或未保存")

    if aof_enabled == "1":
        lines.append("✅ AOF 持久化已开启")
    else:
        lines.append("ℹ️ AOF 持久化未开启 (仅 RDB)")

    # --- 慢日志统计 ---
    slowlog_len = info_dict.get("slowlog_len", "0")
    if int(slowlog_len) > 0:
        lines.append(f"🟡 Redis 慢日志: {slowlog_len} 条 (可用 SLOWLOG GET 查看)")

    lines.append("")
    lines.append("─" * 30 + " 维护建议 " + "─" * 30)
    lines.append("• 定期执行 BGSAVE 确保 RDB 文件更新")
    lines.append("• 监控键空间，避免大键 (BIGKEYS 命令)")
    lines.append("• 生产环境建议开启 AOF 持久化 (appendonly yes)")
    lines.append("• 设置合理的 maxmemory-policy (建议 allkeys-lru)")
    lines.append("=" * 55)

    return "\n".join(lines)


# ========== SQL Server 执行器 ==========

def _exec_sqlcmd(host: str, port: int, user: str, password: str, sql: str, database: str = "master") -> str:
    """通过 sqlcmd 执行 SQL Server 查询"""
    sqlcmd_cmd = "sqlcmd" if not IS_WINDOWS else "sqlcmd.exe"

    cmd = (
        f'{sqlcmd_cmd} -S {host},{port} -U {user} -P {password} '
        f'-d {database} -h -1 -W -s "|" -Q "{sql}"'
    )

    import subprocess
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=15,
            encoding="gbk" if IS_WINDOWS else "utf-8",
            errors="ignore",
        )
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            err_msg = result.stderr.strip() or result.stdout.strip()
            if "Login failed" in err_msg:
                return "__ERROR__: 登录失败，请检查用户名和密码"
            if "Cannot open server" in err_msg:
                return f"__ERROR__: 无法连接到 {host}:{port}"
            return f"__ERROR__: {err_msg[:200]}"
    except subprocess.TimeoutExpired:
        return "__ERROR__: 查询超时"
    except FileNotFoundError:
        return "__ERROR__: sqlcmd 未安装，请安装 SQL Server Command Line Tools"


# ========== Oracle 执行器 ==========

def _exec_sqlplus(host: str, port: int, user: str, password: str, sql: str, service_name: str = "") -> str:
    """通过 sqlplus 执行 Oracle 查询"""
    sqlplus_cmd = "sqlplus" if not IS_WINDOWS else "sqlplus.exe"

    if service_name:
        connect_str = f"{user}/{password}@{host}:{port}/{service_name}"
    else:
        connect_str = f"{user}/{password}@{host}:{port}/orcl"

    # 将 SQL 写入临时文件，避免 shell 转义问题
    import tempfile
    sql_file = os.path.join(tempfile.gettempdir(), "oa_ops_oracle.sql")
    try:
        with open(sql_file, "w", encoding="utf-8") as f:
            f.write(f"SET PAGESIZE 0 FEEDBACK OFF HEADING OFF LINESIZE 500;\n{sql}\nEXIT;\n")

        cmd = f'{sqlplus_cmd} -S {connect_str} @{sql_file}'
        import subprocess
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=15,
                encoding="gbk" if IS_WINDOWS else "utf-8",
                errors="ignore",
            )
            output = (result.stdout or result.stderr or "").strip()
            if "ORA-" in output:
                if "invalid username/password" in output.lower() or "logon denied" in output.lower():
                    return "__ERROR__: Oracle 登录失败，请检查用户名和密码"
                if "TNS:" in output or "no listener" in output.lower():
                    return f"__ERROR__: 无法连接到 {host}:{port}，请检查监听器状态"
                return f"__ERROR__: {output[:200]}"
            if "SP2-" in output or "Unable to resolve" in output:
                return "__ERROR__: sqlplus 无法解析连接字符串"
            return output
        except subprocess.TimeoutExpired:
            return "__ERROR__: 查询超时"
        except FileNotFoundError:
            return "__ERROR__: sqlplus 未安装，请安装 Oracle Instant Client"
    finally:
        try:
            os.remove(sql_file)
        except Exception:
            pass


# ========== SQL Server 检测工具 ==========

@tool
def check_mssql_status(config_text: str) -> str:
    """
    检测 SQL Server 数据库健康状态。

    Args:
        config_text: 数据库连接信息，格式:
            host=127.0.0.1 port=1433 user=sa password=yourpass

    Returns:
        SQL Server 健康报告：版本号、活跃连接数、数据库大小、阻塞会话数、最近备份时间
    """
    params = {}
    for part in config_text.replace("\n", " ").split():
        if "=" in part:
            k, v = part.split("=", 1)
            params[k.strip()] = v.strip()

    host = params.get("host", "127.0.0.1")
    port = int(params.get("port", 1433))
    user = params.get("user", "sa")
    password = params.get("password", "")

    if not password:
        password = os.environ.get("OA_MSSQL_PASSWORD", "")

    if not password:
        return (
            "[提示] 未提供数据库密码。请按以下格式输入:\n"
            "  host=127.0.0.1 port=1433 user=sa password=你的密码"
        )

    lines = [
        "=" * 55,
        f"  SQL Server 数据库健康巡检报告",
        "=" * 55,
        f"目标: {host}:{port}",
        f"用户: {user}",
        f"检测时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 55,
        "",
    ]

    # --- 版本检测 ---
    version_sql = "SELECT @@VERSION"
    result = _exec_sqlcmd(host, port, user, password, version_sql)
    if "__ERROR__" in result:
        lines.append(f"❌ 连接失败: {result.replace('__ERROR__: ', '')}")
        lines.append("")
        lines.append("💡 排查建议:")
        lines.append("  1. 确认 SQL Server 服务已启动")
        lines.append("  2. 确认 TCP/IP 协议已启用（SQL Server 配置管理器）")
        lines.append("  3. 检查防火墙是否放行 {port} 端口")
        lines.append("  4. 确认登录账户具有 CONNECT SQL 权限")
        lines.append("=" * 55)
        return "\n".join(lines)

    version = result.split("|")[0].strip() if "|" in result else result.strip()
    lines.append(f"✅ 连接成功 — {version[:100]}")
    lines.append("")

    # --- 活跃连接数 ---
    conn_sql = (
        "SELECT COUNT(*) FROM sys.dm_exec_connections "
        "SELECT COUNT(*) FROM sys.dm_exec_sessions WHERE is_user_process = 1 "
        "SELECT COUNT(*) FROM sys.dm_exec_requests WHERE blocking_session_id > 0"
    )
    try:
        conn_result = _exec_sqlcmd(host, port, user, password, conn_sql)
        if conn_result and not conn_result.startswith("__ERROR__"):
            conn_parts = [p.strip() for p in conn_result.split("|") if p.strip()]
            if len(conn_parts) >= 3:
                lines.append(f"🔗 活跃连接: {conn_parts[0]} 个")
                lines.append(f"   活跃会话: {conn_parts[1]} 个")
                if int(conn_parts[2]) > 0:
                    lines.append(f"   ⚠️ 阻塞会话: {conn_parts[2]} 个（存在锁等待！）")
                else:
                    lines.append(f"   阻塞会话: 0 个")
    except Exception:
        pass

    # --- 数据库大小 TOP 5 ---
    size_sql = (
        "SELECT TOP 5 name, "
        "CAST(ROUND(SUM(size) * 8 / 1024.0, 1) AS VARCHAR) + ' MB' AS total_size "
        "FROM sys.master_files "
        "WHERE database_id > 4 "
        "GROUP BY name ORDER BY SUM(size) DESC"
    )
    try:
        size_result = _exec_sqlcmd(host, port, user, password, size_sql)
        if size_result and not size_result.startswith("__ERROR__"):
            lines.append("")
            lines.append("📊 数据库大小 TOP 5:")
            for row in size_result.split("\n")[:5]:
                row = row.strip()
                if row:
                    lines.append(f"   {row.replace('|', ' — ')}")
    except Exception:
        pass

    # --- 最近备份时间 ---
    backup_sql = (
        "SELECT TOP 5 name, "
        "ISNULL(CONVERT(VARCHAR, MAX(backup_finish_date), 120), 'NEVER') AS last_backup "
        "FROM sys.databases d "
        "LEFT JOIN msdb.dbo.backupset b ON d.name = b.database_name "
        "WHERE d.database_id > 4 "
        "GROUP BY name ORDER BY MAX(backup_finish_date)"
    )
    try:
        backup_result = _exec_sqlcmd(host, port, user, password, backup_sql)
        if backup_result and not backup_result.startswith("__ERROR__"):
            lines.append("")
            lines.append("💾 最近备份时间:")
            for row in backup_result.split("\n")[:5]:
                row = row.strip()
                if row:
                    parts = row.split("|")
                    if len(parts) >= 2:
                        db_name = parts[0].strip()
                        last_backup = parts[1].strip()
                        icon = "🔴" if last_backup == "NEVER" else ("🟡" if " " in last_backup and last_backup < "2025" else "✅")
                        lines.append(f"   {icon} {db_name}: {last_backup}")
    except Exception:
        pass

    lines.append("")
    lines.append("─" * 30 + " 维护建议 " + "─" * 30)
    lines.append("• 定期执行索引维护: ALTER INDEX ... REBUILD")
    lines.append("• 更新统计信息: EXEC sp_updatestats")
    lines.append("• 监控事务日志大小，避免日志文件撑满磁盘")
    lines.append("• 配置定期备份作业: FULL (每周) + DIFF (每日) + LOG (每小时)")
    lines.append("=" * 55)

    return "\n".join(lines)


# ========== Oracle 检测工具 ==========

@tool
def check_oracle_status(config_text: str) -> str:
    """
    检测 Oracle 数据库健康状态。

    Args:
        config_text: 数据库连接信息，格式:
            host=127.0.0.1 port=1521 user=system password=yourpass service=orcl

    Returns:
        Oracle 健康报告：版本号、实例状态、表空间使用率、活跃会话数、归档日志状态
    """
    params = {}
    for part in config_text.replace("\n", " ").split():
        if "=" in part:
            k, v = part.split("=", 1)
            params[k.strip()] = v.strip()

    host = params.get("host", "127.0.0.1")
    port = int(params.get("port", 1521))
    user = params.get("user", "system")
    password = params.get("password", "")
    service_name = params.get("service", "orcl")

    if not password:
        password = os.environ.get("OA_ORACLE_PASSWORD", "")

    if not password:
        return (
            "[提示] 未提供数据库密码。请按以下格式输入:\n"
            "  host=127.0.0.1 port=1521 user=system password=你的密码 service=orcl"
        )

    lines = [
        "=" * 55,
        f"  Oracle 数据库健康巡检报告",
        "=" * 55,
        f"目标: {host}:{port}/{service_name}",
        f"用户: {user}",
        f"检测时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 55,
        "",
    ]

    # --- 版本 + 实例状态 ---
    version_sql = (
        "SELECT 'VERSION:' || banner FROM v$version WHERE ROWNUM = 1; "
        "SELECT 'INSTANCE:' || instance_name || ' | STATUS:' || status || ' | HOST:' || host_name "
        "FROM v$instance; "
        "SELECT 'STARTUP:' || TO_CHAR(startup_time, 'YYYY-MM-DD HH24:MI:SS') FROM v$instance;"
    )
    result = _exec_sqlplus(host, port, user, password, version_sql, service_name)
    if "__ERROR__" in result:
        lines.append(f"❌ 连接失败: {result.replace('__ERROR__: ', '')}")
        lines.append("")
        lines.append("💡 排查建议:")
        lines.append("  1. 确认 Oracle 实例已启动: lsnrctl status")
        lines.append("  2. 确认监听器端口: lsnrctl status | grep PORT")
        lines.append("  3. 检查防火墙是否放行 {port} 端口")
        lines.append("  4. 确认 tnsnames.ora 配置或 Easy Connect 格式")
        lines.append("=" * 55)
        return "\n".join(lines)

    # 解析基本信息
    for line in result.split("\n"):
        line = line.strip()
        if line.startswith("VERSION:"):
            lines.append(f"✅ {line}")
        elif line.startswith("INSTANCE:"):
            lines.append(f"📋 {line}")
        elif line.startswith("STARTUP:"):
            lines.append(f"⏱️ {line}")

    lines.append("")

    # --- 表空间使用率 ---
    ts_sql = (
        "SELECT TABLESPACE_NAME || '|' || "
        "ROUND(USED_SPACE_MB,0) || 'MB|' || "
        "ROUND(TOTAL_SPACE_MB,0) || 'MB|' || "
        "ROUND(USED_PCT,1) || '%|' || "
        "CASE WHEN USED_PCT > 90 THEN 'CRITICAL' WHEN USED_PCT > 80 THEN 'WARNING' ELSE 'OK' END "
        "FROM (SELECT "
        "  d.tablespace_name, "
        "  SUM(d.bytes)/1024/1024 AS TOTAL_SPACE_MB, "
        "  SUM(NVL(f.bytes,0))/1024/1024 AS FREE_SPACE_MB, "
        "  SUM(d.bytes)/1024/1024 - SUM(NVL(f.bytes,0))/1024/1024 AS USED_SPACE_MB, "
        "  (SUM(d.bytes) - SUM(NVL(f.bytes,0)))/SUM(d.bytes)*100 AS USED_PCT "
        "FROM dba_data_files d "
        "LEFT JOIN dba_free_space f ON d.tablespace_name = f.tablespace_name "
        "GROUP BY d.tablespace_name) ORDER BY USED_PCT DESC"
    )
    try:
        ts_result = _exec_sqlplus(host, port, user, password, ts_sql, service_name)
        if ts_result and not ts_result.startswith("__ERROR__"):
            lines.append("📊 表空间使用率:")
            for row in ts_result.split("\n")[:10]:
                row = row.strip()
                if row:
                    parts = row.split("|")
                    if len(parts) >= 5:
                        ts_name = parts[0].strip()
                        used = parts[1].strip()
                        total = parts[2].strip()
                        pct = parts[3].strip()
                        status = parts[4].strip()
                        icon = {"CRITICAL": "🔴", "WARNING": "🟡", "OK": "✅"}.get(status, "")
                        lines.append(f"   {icon} {ts_name}: {used}/{total} ({pct})")
    except Exception:
        pass

    # --- 活跃会话 ---
    sess_sql = (
        "SELECT COUNT(*) || ' total sessions' FROM v$session; "
        "SELECT COUNT(*) || ' active sessions' FROM v$session WHERE status = 'ACTIVE'; "
        "SELECT COUNT(*) || ' blocking sessions' FROM v$session WHERE blocking_session IS NOT NULL;"
    )
    try:
        sess_result = _exec_sqlplus(host, port, user, password, sess_sql, service_name)
        if sess_result and not sess_result.startswith("__ERROR__"):
            lines.append("")
            lines.append("🔗 会话统计:")
            for row in sess_result.split("\n"):
                row = row.strip()
                if row and "ORA-" not in row:
                    lines.append(f"   {row}")
    except Exception:
        pass

    # --- 归档日志状态 ---
    arch_sql = (
        "SELECT 'LOG_MODE: ' || log_mode FROM v$database; "
        "SELECT 'ARCHIVE_DEST: ' || DEST_NAME || ' | STATUS: ' || STATUS "
        "FROM v$archive_dest WHERE STATUS != 'INACTIVE' AND ROWNUM <= 3;"
    )
    try:
        arch_result = _exec_sqlplus(host, port, user, password, arch_sql, service_name)
        if arch_result and not arch_result.startswith("__ERROR__"):
            lines.append("")
            for row in arch_result.split("\n"):
                row = row.strip()
                if row and "ORA-" not in row:
                    if "LOG_MODE:" in row:
                        if "ARCHIVELOG" in row.upper():
                            lines.append(f"✅ {row}")
                        else:
                            lines.append(f"🔴 {row} (建议开启归档模式！)")
                    elif "ARCHIVE_DEST:" in row:
                        if "VALID" in row.upper():
                            lines.append(f"✅ {row}")
                        elif "ERROR" in row.upper():
                            lines.append(f"🔴 {row}")
                        else:
                            lines.append(f"   {row}")
    except Exception:
        pass

    lines.append("")
    lines.append("─" * 30 + " 维护建议 " + "─" * 30)
    lines.append("• 监控表空间使用率，设置自动扩展或提前扩容")
    lines.append("• 生产环境必须开启归档日志模式（ARCHIVELOG）")
    lines.append("• 定期收集统计信息: EXEC DBMS_STATS.GATHER_DATABASE_STATS;")
    lines.append("• 使用 RMAN 配置定期备份策略")
    lines.append("=" * 55)

    return "\n".join(lines)


# ========== 工具汇总 ==========

DB_INSPECTION_TOOLS = [
    check_mysql_status,
    show_mysql_slow_queries,
    check_mssql_status,
    check_oracle_status,
    check_redis_status,
]

# ========== LLM Agent ==========

DB_SYSTEM_PROMPT = """你是一名资深DBA，负责数据库健康巡检和故障诊断。

你可以使用的工具：
- check_mysql_status: 检测 MySQL 健康状态（连接、慢查询、缓冲池、主从延迟）
- show_mysql_slow_queries: 查看 MySQL 最近慢查询详情
- check_mssql_status: 检测 SQL Server 健康状态（连接数、阻塞、备份状态）
- check_oracle_status: 检测 Oracle 健康状态（表空间、会话、归档日志）
- check_redis_status: 检测 Redis 健康状态（内存、命中率、持久化、键统计）

诊断要点：
1. MySQL 连接数接近 max_connections 时需要扩容或排查连接泄漏
2. MySQL InnoDB 缓冲池命中率应 > 99%，慢查询应定期审查并优化
3. SQL Server 阻塞会话应持续监控，长期阻塞需 kill 会话
4. Oracle 表空间使用率 > 90% 需立即扩容，归档日志模式必须开启
5. Redis 缓存命中率应 > 95%，持久化必须正常配置

请根据用户输入，调用合适的工具，生成数据库健康报告和改进建议。"""


class DBCheckAgent:
    """
    数据库巡检 Agent（LLM 增强版）。

    使用方式：
        agent = DBCheckAgent(llm_api_key="...")
        print(agent.check_mysql("127.0.0.1", 3306, "root", "password"))
        print(agent.check_redis("127.0.0.1", 6379, ""))
    """

    def __init__(
        self,
        llm_api_key: str = "",
        llm_base_url: str = "https://api.deepseek.com/v1",
        llm_model: str = "deepseek-chat",
    ):
        self.llm_api_key = llm_api_key
        self.llm_base_url = llm_base_url
        self.llm_model = llm_model
        self._agent = None

        if llm_api_key and llm_api_key not in ("ollama", "your-api-key-here", ""):
            self._init_agent()

    def _init_agent(self):
        try:
            from langchain_openai import ChatOpenAI
            from langchain.agents import create_agent

            llm = ChatOpenAI(
                api_key=self.llm_api_key,
                base_url=self.llm_base_url,
                model=self.llm_model,
                temperature=0.2,
                max_tokens=2048,
            )
            self._agent = create_agent(
                model=llm,
                tools=DB_INSPECTION_TOOLS,
                system_prompt=DB_SYSTEM_PROMPT,
            )
            logger.info("数据库巡检Agent初始化完成")
        except Exception as e:
            logger.warning(f"数据库巡检Agent初始化失败: {e}")

    def check_mysql(self, host: str, port: int, user: str, password: str) -> str:
        """检测 MySQL 健康状态"""
        config_text = f"host={host} port={port} user={user} password={password}"
        if self._agent:
            try:
                result = self._agent.invoke({
                    "messages": [{"role": "user", "content": f"请检测 MySQL 数据库 {host}:{port} 的健康状态"}]
                })
                msgs = result.get("messages", [])
                if msgs:
                    return msgs[-1].content
            except Exception as e:
                logger.warning(f"Agent MySQL 检测降级: {e}")
        return check_mysql_status.invoke({"config_text": config_text})

    def check_mssql(self, host: str, port: int, user: str, password: str) -> str:
        """检测 SQL Server 健康状态"""
        config_text = f"host={host} port={port} user={user} password={password}"
        if self._agent:
            try:
                result = self._agent.invoke({
                    "messages": [{"role": "user", "content": f"请检测 SQL Server {host}:{port} 的健康状态"}]
                })
                msgs = result.get("messages", [])
                if msgs:
                    return msgs[-1].content
            except Exception as e:
                logger.warning(f"Agent MSSQL 检测降级: {e}")
        return check_mssql_status.invoke({"config_text": config_text})

    def check_oracle(self, host: str, port: int, user: str, password: str, service: str = "orcl") -> str:
        """检测 Oracle 健康状态"""
        config_text = f"host={host} port={port} user={user} password={password} service={service}"
        return check_oracle_status.invoke({"config_text": config_text})

    def check_redis(self, host: str, port: int, password: str = "") -> str:
        """检测 Redis 健康状态"""
        config_text = f"host={host} port={port} password={password}"
        return check_redis_status.invoke({"config_text": config_text})

    def show_slow_queries(self, host: str, port: int, user: str, password: str, limit: int = 10) -> str:
        """查看慢查询"""
        config_text = f"host={host} port={port} user={user} password={password} limit={limit}"
        return show_mysql_slow_queries.invoke({"config_text": config_text})


# ========== 快速测试入口 ==========

if __name__ == "__main__":
    print("=== 数据库巡检模块测试 ===\n")

    print("⚠️ 需要本地安装 mysql 和 redis-cli 客户端才能测试")
    print("   或配置 SSH 巡检模式通过远程服务器执行")
    print()
    print("使用示例:")
    print("  agent = DBCheckAgent()")
    print("  result = agent.check_mysql('127.0.0.1', 3306, 'root', 'yourpass')")
    print("  print(result)")
    print()
    print("  # Redis:")
    print("  result = agent.check_redis('127.0.0.1', 6379)")
    print("  print(result)")
