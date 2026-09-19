# MySQL慢查询与连接数问题处置指南

**适用实例**：OA 平台 MySQL 8.0，主库 db-oa-01（10.20.30.11），从库 db-oa-02（10.20.30.12）。
**慢日志路径**：/var/log/mysql/slow.log

## 1. 概述
本指南针对两类高频问题：查询响应慢（用户感知为页面卡顿、请求超时）与连接数异常（应用报 1040 或连接池耗尽）。两者常互为因果——慢查询长期占用连接，会把连接数逐步推到上限，因此排查时不要只盯一个方向。

## 2. 慢查询日志开启与确认
临时开启（重启失效）：
```
SET GLOBAL slow_query_log = ON;
SET GLOBAL long_query_time = 1;
SET GLOBAL log_queries_not_using_indexes = OFF;
```
持久化写入 /etc/mysql/my.cnf：
```
[mysqld]
slow_query_log = 1
slow_query_log_file = /var/log/mysql/slow.log
long_query_time = 1
```
确认当前值：`SHOW VARIABLES LIKE 'slow_query_log%';` 与 `SHOW VARIABLES LIKE 'long_query_time';`。

## 3. 慢日志分析方法
- 快速统计：`mysqldumpslow -s t -t 20 /var/log/mysql/slow.log`，按总耗时排序取前 20 条；
- 细粒度分析：`pt-query-digest /var/log/mysql/slow.log`，输出按响应时间占比排序的报告；
- 重点指标：Query_time、Lock_time，以及 Rows_examined 与 Rows_sent 的比值，比值过大通常说明索引使用不当。

## 4. EXPLAIN 要点
对可疑 SQL 执行 `EXPLAIN SELECT ...`，重点看四列：

| 字段 | 关注点 |
| --- | --- |
| type | 出现 ALL 即全表扫描，目标至少到 range 或 ref |
| key | 为 NULL 表示未使用索引 |
| rows | 预估扫描行数，越大越危险 |
| Extra | 出现 Using filesort 或 Using temporary 需优化 |

## 5. max_connections 与连接池
- 查看上限：`SHOW VARIABLES LIKE 'max_connections';`，生产实例建议 800；
- 查看已用：`SHOW STATUS LIKE 'Threads_connected';`；
- 应用侧连接池上限（maxActive 或 maximumPoolSize）应略小于数据库上限，为巡检与应急预留连接；
- 常见误配：多个应用共用一台实例，各自连接池上限之和超过 max_connections，高峰期相互挤占导致偶发连不上。

## 6. 锁等待与阻塞定位
- 锁等待超时参数：`SHOW VARIABLES LIKE 'innodb_lock_wait_timeout';`，默认 50 秒，OLTP 场景建议调整到 10 至 20 秒；
- 定位阻塞源头：MySQL 8.0 可查 performance_schema.data_lock_waits，或通过 information_schema 查看事务等待关系；
- 找到长时间未提交的会话后，先与业务确认再 `KILL <session_id>;`，禁止未经确认批量清理会话。

## 7. 用 SHOW PROCESSLIST 定位问题
执行 `SHOW FULL PROCESSLIST;`，重点看三列：
1. Time：同一条 SQL 运行超过 60 秒即为异常；
2. State：大量会话堆积在 Waiting for table metadata lock，说明有 DDL 在阻塞；
3. Info：出现未加索引的模糊查询（如 LIKE 以百分号开头）多为新上线的 SQL。

## 8. 预防措施
- 上线前必须过 SQL 审核，禁止 SELECT 星号与无索引的大表关联；
- 每日定时采集慢日志 TOP 10 并推送负责人跟进；
- 连接池配置合理的空闲回收，例如 idleTimeout 60 秒、maxLifetime 30 分钟；
- 大表 DDL 使用 gh-ost 或 pt-online-schema-change，避开业务高峰执行。

## 9. 附录：常用参数速查
| 参数 | 默认值 | 生产建议 |
| --- | --- | --- |
| long_query_time | 10 | 1 |
| max_connections | 151 | 800 |
| innodb_lock_wait_timeout | 50 | 20 |
| wait_timeout | 28800 | 3600 |
