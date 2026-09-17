# SQLServer备份与还原操作指南

**适用实例**：mssql-oa-01（10.20.30.51），SQL Server 2019；备份盘为 D:\backup，备份作业由 SQL Server 代理按计划执行。

## 1. 概述
本指南规定 OA 平台 SQL Server 数据库的备份策略、标准命令与还原流程，覆盖全量、差异、事务日志备份的落地方式，时间点还原（STOPAT）的用法，以及还原失败的常见排查。所有生产还原操作必须先申请变更、明确回退方案。

## 2. 备份策略

| 类型 | 频率 | 保留期 | 说明 |
| --- | --- | --- | --- |
| 全量 | 每周日 01:00 | 4 周 | BACKUP DATABASE |
| 差异 | 每日 01:00（周一至周六） | 2 周 | 基于最近一次全备 |
| 事务日志 | 每 15 分钟 | 7 天 | 要求完整恢复模式 |

前提条件：数据库恢复模式为 FULL；备份前确认备份盘剩余空间不低于预估备份体积的两倍。

## 3. 备份命令

```
-- 全量备份
BACKUP DATABASE oa_flow
  TO DISK = 'D:\backup\oa_flow_full_20260913.bak'
  WITH INIT, COMPRESSION, CHECKSUM, STATS = 10;

-- 差异备份
BACKUP DATABASE oa_flow
  TO DISK = 'D:\backup\oa_flow_diff_20260914.bak'
  WITH DIFFERENTIAL, COMPRESSION, INIT;

-- 事务日志备份
BACKUP LOG oa_flow
  TO DISK = 'D:\backup\oa_flow_log_20260914_1015.trn'
  WITH INIT, COMPRESSION;
```

备份后验证完整性：`RESTORE VERIFYONLY FROM DISK='D:\backup\oa_flow_full_20260913.bak';`

## 4. 还原操作
1. 先查看备份集信息：`RESTORE HEADERONLY FROM DISK='...';` 与 `RESTORE FILELISTONLY FROM DISK='...';`
2. 全量还原并挪动物理文件，避免覆盖生产文件：
```
RESTORE DATABASE oa_flow_new
  FROM DISK = 'D:\backup\oa_flow_full_20260913.bak'
  WITH MOVE 'oa_flow' TO 'E:\data\oa_flow_new.mdf',
       MOVE 'oa_flow_log' TO 'E:\data\oa_flow_new_log.ldf',
       NORECOVERY, REPLACE;
```
3. 随后按顺序追加差异备份与日志备份，全部使用 NORECOVERY，最后以 WITH RECOVERY 结束滚动。

## 5. 还原到时间点
场景：误删数据需要回退到上午 10:05。

```
RESTORE LOG oa_flow_new
  FROM DISK = 'D:\backup\oa_flow_log_20260914_1015.trn'
  WITH STOPAT = '2026-09-14T10:05:00', RECOVERY;
```

注意：STOPAT 只能在日志备份覆盖的时间范围内生效；若目标时间点晚于最后一个日志备份，则数据无法恢复，这正是日志备份必须连续、不可断档的原因。

## 6. 还原失败的常见原因

| 报错或现象 | 原因 | 处置 |
| --- | --- | --- |
| 无法获得对数据库的独占访问 | 目标库仍有活动连接 | 置为单用户模式再还原 |
| 媒体集有 2 个媒体簇但只提供了 1 个 | 备份被拆分到多个文件 | 提供完整备份集 |
| 数据库版本不兼容 | 高版本备份还原到低版本实例 | 目标实例版本需不低于源库 |
| 对备份文件访问被拒绝 | 服务账号缺少读权限 | 授予服务账号文件权限 |
| 指定的路径不存在 | MOVE 目标目录未创建 | 先创建目标目录 |

获得独占访问的语句：`ALTER DATABASE oa_flow SET SINGLE_USER WITH ROLLBACK IMMEDIATE;`

## 7. 预防措施
- 每月安排一次还原演练，还原至隔离实例并做数据一致性核对；
- 备份作业失败必须触发告警，禁止静默跳过；
- 备份文件异地保留一份，可拷贝至备份一体机或对象存储；
- 还原演练记录归档，包含备份集时间、还原耗时与校验结论。

## 8. 附录：常用命令速查

| 用途 | 命令 |
| --- | --- |
| 验证备份集 | RESTORE VERIFYONLY |
| 查看备份头信息 | RESTORE HEADERONLY |
| 查看文件列表 | RESTORE FILELISTONLY |
| 查看恢复模式 | SELECT name, recovery_model_desc FROM sys.databases; |
