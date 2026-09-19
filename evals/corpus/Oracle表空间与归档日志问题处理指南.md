# Oracle表空间与归档日志问题处理指南

**适用实例**：orcl-oa（10.20.30.31），Oracle 19c，已开启归档模式；备份文件经网络存放至备份主机 bk-oa-01（10.20.30.41）。

## 1. 概述
空间类问题是 Oracle 生产库最常见的故障源：表空间写满导致单据无法插入、临时表空间不足导致报表查询报错、归档区写满导致数据库挂起。归档满时客户端通常报 ORA-00257: archiver error. Connect internal only, until freed，此时普通业务账号已无法连接，属于最高优先级的处置场景。

## 2. 表空间使用率核查
1. 查看数据文件与剩余空间：
```
SELECT d.tablespace_name,
       ROUND(d.bytes/1024/1024) AS total_mb,
       ROUND(f.bytes/1024/1024) AS free_mb
  FROM dba_data_files d
  LEFT JOIN dba_free_space f ON d.tablespace_name = f.tablespace_name;
```
2. 使用率超过 85% 即纳入扩容计划，超过 95% 立即处置；
3. 注意区分普通表空间与 undo 表空间，两者扩容策略不同。

## 3. 表空间扩容
优先启用数据文件自动扩展，其次新增数据文件：
```
ALTER TABLESPACE OA_DATA
  ADD DATAFILE '/u01/app/oracle/oradata/orcloa/oa_data02.dbf'
  SIZE 2G AUTOEXTEND ON NEXT 256M MAXSIZE 16G;
```
注意事项：
- 单个数据文件建议不超过 32G（8K 块大小限制），数据量大时用多文件分担；
- 新增前确认操作系统层剩余空间充足；
- 启用 autoextend 时必须设置 MAXSIZE 上限，防止无声写满整个文件系统。

## 4. 临时表空间不足
现象：查询报 ORA-01652: unable to extend temp segment。
处置步骤：
1. 查看临时段占用：`SELECT * FROM v$tempseg_usage;`
2. 扩容临时表空间：
```
ALTER TABLESPACE TEMP ADD TEMPFILE '/u01/app/oracle/oradata/orcloa/temp02.dbf'
  SIZE 4G AUTOEXTEND ON NEXT 512M MAXSIZE 20G;
```
3. 结合 v$sqlarea 按磁盘读排序定位大排序 SQL 并优化；
4. 必要时新建临时表空间并切换默认表空间，操作须放在业务低峰期。

## 5. 归档日志满的处置
关键前提：清理归档前必须确认备份已完成。
1. 核查归档区：`SELECT * FROM v$recovery_file_dest;`
2. 确认最近一次 RMAN 全备成功：`LIST BACKUP SUMMARY;`
3. 使用 RMAN 清理过期归档：
```
RMAN> CROSSCHECK ARCHIVELOG ALL;
RMAN> DELETE ARCHIVELOG ALL COMPLETED BEFORE 'SYSDATE-7';
```
4. 禁止在操作系统层直接删除归档文件，否则会破坏控制文件记录，导致后续 RMAN 报错。

## 6. RMAN 备份简述
- 全量备份：`BACKUP DATABASE PLUS ARCHIVELOG;`，每周执行一次；
- 增量备份：`BACKUP INCREMENTAL LEVEL 1 DATABASE;`，每日执行；
- 保留策略：最近 2 周全备加 1 个月归档；
- 可恢复性校验：`RESTORE DATABASE VALIDATE;`，每月在备用环境校验一次。

## 7. 预防措施
- 归档目录单独挂盘，或直接落在备份主机的网络存储，避免与数据文件争用空间；
- 监控两项硬指标：归档目录剩余空间低于 20% 告警、表空间使用率高于 85% 告警；
- 大表批量操作前先评估 undo 与归档增量，分批提交；
- 变更窗口新增数据文件后，同步更新巡检脚本的核查清单。

## 8. 附录：常用命令速查
| 场景 | 命令 |
| --- | --- |
| 查看归档模式 | ARCHIVE LOG LIST; |
| 手动切换日志 | ALTER SYSTEM SWITCH LOGFILE; |
| 查看数据文件 | SELECT name, bytes FROM v$datafile; |
| 查看临时文件 | SELECT name, bytes FROM v$tempfile; |
