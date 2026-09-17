# Oracle数据库日常故障处置手册

**适用实例**：OA 核心库 orcl-oa，主机 ora-oa-01（10.20.30.31），Oracle 19c，监听端口 1521。
**关键路径**：ORACLE_BASE 为 /u01/app/oracle，告警日志位于 $ORACLE_BASE/diag/rdbms/orcloa/orcloa/trace 目录下。

## 1. 概述
本手册面向 OA 平台 Oracle 单实例（含 DataGuard 备库）的日常故障处置，覆盖监听异常、空间类报错、会话与锁三个方向，供值班人员与 DBA 共用。总体要求：先看告警日志与监听状态，再判断属于哪一类问题。

## 2. 监听状态检查
1. 查看监听：`lsnrctl status`，确认监听器与实例的注册状态正常；
2. 服务名未注册时，先 `lsnrctl reload`，或在数据库内执行 `ALTER SYSTEM REGISTER;`；
3. 客户端报 ORA-12541: TNS no listener，说明监听未启动，执行 `lsnrctl start`；
4. 客户端报 ORA-12514: TNS listener does not currently know of service，多为服务名拼写错误或实例尚未注册；
5. 用 tnsping 验证 TNS 配置解析与网络连通。

## 3. 常见错误代码处置

| 错误码 | 含义 | 常见诱因 | 处置方向 |
| --- | --- | --- | --- |
| ORA-01555 | 快照过旧 | undo 空间不足或查询执行过久 | 扩容 undo，优化长查询 |
| ORA-04031 | 无法分配共享内存 | 共享池碎片或内存参数偏小 | 调整共享池相关参数 |
| ORA-01652 | 临时段无法扩展 | 临时表空间不足或大排序 | 扩容 temp，排查大排序 SQL |
| ORA-28000 | 账号被锁定 | 连续输错密码触发策略 | 解锁账号并复查应用配置 |

补充说明：ORA-04031 在业务版本升级后集中出现时，往往是新 SQL 大量硬解析所致，可通过绑定变量改造缓解。

## 4. 会话与锁排查
- 当前用户会话总数：`SELECT COUNT(*) FROM v$session WHERE type='USER';`
- 活跃且运行时间长的会话：
```
SELECT sid, serial#, username, status, sql_id,
       last_call_et/60 AS minutes_running
  FROM v$session
 WHERE type='USER' AND status='ACTIVE'
 ORDER BY last_call_et DESC;
```
- 锁分析：查询 v$lock 中 type 为 TM 或 TX 的记录，关联 v$session 找出阻塞源头；
- 常见结论：阻塞者多为长时间未提交的应用事务，或 DDL 与 DML 相互等待；
- 处置：与业务确认影响后执行 `ALTER SYSTEM KILL SESSION 'sid,serial#' IMMEDIATE;`。

## 5. 日常巡检要点
1. 告警日志中是否出现 ORA- 开头的报错，建议每日上午定时巡检；
2. 各类表空间（含 undo、temp）使用率超过 85% 即预警；
3. 归档目录剩余空间与归档生成速率；
4. 无效对象与失效索引的数量变化；
5. DataGuard 备库同步延迟，可查询 v$dataguard_stats 中的 apply lag。

## 6. 预防措施
- 核心库参数变更必须走变更单，禁止在生产直接调整内存参数；
- 会话空闲超时参数按业务连接池实际设置，避免空闲会话堆积；
- 每周对告警日志做一次关键字统计，识别重复出现的报错；
- 每季度演练一次故障切换，验证备库接管流程可用。

## 7. 附录：常用视图清单
| 视图 | 用途 |
| --- | --- |
| v$session | 会话明细与等待事件 |
| v$lock | 锁信息与阻塞关系 |
| v$sqlarea | 共享池内 SQL 统计 |
| dba_free_space | 表空间剩余空间 |
| v$undostat | undo 使用与快照过旧统计 |
