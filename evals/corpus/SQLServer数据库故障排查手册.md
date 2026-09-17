# SQLServer数据库故障排查手册

**适用实例**：OA 流程库与报表库，SQL Server 2019，主机 mssql-oa-01（10.20.30.51），默认实例，端口 1433。
**关键路径**：错误日志位于 SQL Server 安装目录的 MSSQL\Log 子目录下，当前日志文件名为 ERRORLOG。

## 1. 概述
SQL Server 常见故障集中在四类：服务或实例不可用、事务日志写满（9002）、连接与权限类报错、tempdb 与阻塞问题。本手册给出相应的排查路径与处置顺序，适用于值班人员的第一轮定位。

## 2. 服务与实例检查
1. 用 SQL Server 配置管理器确认 SQL Server (MSSQLSERVER) 与 SQL Server 代理两个服务均处于运行状态；
2. 命令行复核：`sc query MSSQLSERVER`，观察 STATE 是否为 RUNNING；
3. 网络连通性：`Test-NetConnection -ComputerName 10.20.30.51 -Port 1433`；
4. 登录验证：`sqlcmd -S 10.20.30.51 -U oa_app -Q "SELECT @@VERSION;"`；
5. 命名实例场景下，确认 SQL Browser 服务已启动且防火墙放行了对应端口。

## 3. 错误日志排查
1. SSMS 中依次展开"管理"与"SQL Server 日志"，查看当前日志；
2. 也可直接读取 ERRORLOG 文件，历史文件按启动次数轮转，命名为 ERRORLOG.1、ERRORLOG.2；
3. 需要关注的典型关键字：Login failed for user（认证失败）、I/O error（磁盘异常）、Recovery completed（恢复完成）。

## 4. 常见错误与处置

| 错误号 | 消息摘要 | 常见原因 | 处置方向 |
| --- | --- | --- | --- |
| 9002 | The transaction log for database is full | 日志长期未备份、大事务 | 立即做日志备份或扩容 |
| 4060 | Cannot open database requested by the login | 数据库脱机或权限缺失 | 核对库状态与用户映射 |
| 15130 | 数据库已存在，请选择其他名称 | 附加或还原时重名 | 更换目标库名或先删除 |
| 4064 | 无法打开用户默认数据库 | 默认库不存在或不可用 | 修正登录的默认数据库 |

## 5. 事务日志满（9002）应急处置
前提：数据库处于完整恢复模式。
1. 第一时间执行日志备份：`BACKUP LOG oa_flow TO DISK='D:\backup\oa_flow_log.trn';`
2. 若日志因长事务（如未提交的批量更新）涨满，先在活动监视器中定位并终止该会话；
3. 紧急情况可临时收缩：`DBCC SHRINKFILE (oa_flow_log, 2048);`，但收缩会破坏文件物理连续性，仅作应急手段；
4. 长期方案：业务允许时改用简单恢复模式，或将日志自动增长步长调整为 512MB 并设上限。

## 6. tempdb 满的排查
现象：报错提示 tempdb 可用空间不足，或 Could not allocate a new page for database tempdb。
- 占用统计：
```
SELECT session_id, user_objects_alloc_page_count
  FROM sys.dm_db_session_space_usage
 ORDER BY 2 DESC;
```
- 常见原因：超大排序、版本存储膨胀、过度使用临时表，或查询计划产生巨大哈希连接；
- 处置：重启实例可快速释放，但必须先定位占用会话；长期应把 tempdb 文件数调整为 CPU 核数的四分之一到二分之一。

## 7. 阻塞与死锁初步排查
- 定位阻塞链：
```
SELECT blocking_session_id, session_id, wait_type, wait_time
  FROM sys.dm_exec_requests
 WHERE blocking_session_id <> 0;
```
- 死锁捕获：开启 1222 跟踪标志或使用扩展事件抓取死锁图；
- 初步结论：多数阻塞来自长事务或缺失索引的更新语句（大范围扫描触发锁升级）；
- 处置：确认业务后终止阻塞会话 `KILL <spid>;`，事后补充索引或拆分事务。

## 8. 预防措施
- 生产库巡检固定动作：日志可用空间、tempdb 使用率、最长阻塞链时长；
- 报表类查询与联机业务分离，报表走只读副本或快照隔离；
- 所有批处理任务必须加超时与重试，禁止无界事务；
- 错误日志至少保留 30 天副本，便于事后追溯。

## 9. 附录：快速定位入口
| 排查目标 | 工具或视图 |
| --- | --- |
| 当前活动请求 | sys.dm_exec_requests |
| 空间占用 | sys.dm_db_file_space_usage |
| 索引碎片 | sys.dm_db_index_physical_stats |
| 连接会话 | sys.dm_exec_sessions |
