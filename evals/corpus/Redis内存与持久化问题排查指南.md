# Redis内存与持久化问题排查指南

**适用实例**：oa-cache-01（10.20.30.21）与 oa-cache-02（10.20.30.22），Redis 6.2，数据目录 /var/lib/redis。

## 1. 概述
内存与持久化是 Redis 最容易出现慢性故障的两个方向：内存缓慢增长、碎片率升高、快照落盘失败、磁盘被写满。这类问题通常不立刻爆发，但一旦叠加业务高峰就会造成写入被拒。本指南给出从现象到处置的完整路径。

## 2. RDB 持久化配置与问题
配置项位于 /etc/redis/redis.conf：

| 配置项 | 建议值 | 说明 |
| --- | --- | --- |
| save | 900 1 / 300 10 / 60 10000 | 触发快照的写入条件 |
| stop-writes-on-bgsave-error | yes | 落盘失败即停止写入 |
| rdbcompression | yes | 压缩快照文件 |
| dbfilename | dump.rdb | 快照文件名 |
| dir | /var/lib/redis | 落盘目录 |

典型问题：后台保存失败时日志出现 Background saving error，随后写命令返回 MISCONF Redis is configured to save RDB snapshots, but it is currently not able to persist on disk。多数情况是磁盘写满或目录权限被改，先 `df -h` 检查磁盘，再修正目录权限。

## 3. AOF 相关问题
- 开启方式：appendonly yes，appendfsync everysec 是性能与安全的折中；
- 手动重写：`BGREWRITEAOF`，重写期间内存占用会上升，应避开业务高峰；
- 常见告警 AOF write error：多为磁盘满或文件系统被挂成只读；
- 启用 aof-use-rdb-preamble 时，AOF 文件前半段为 RDB 格式，加载速度更快。

## 4. fork 阻塞排查
RDB 保存与 AOF 重写都依赖 fork。实例内存较大（如 16G 以上）时，fork 可能阻塞主线程数百毫秒，表现为业务偶发超时。排查手段：
- `INFO stats` 查看 latest_fork_usec，即最近一次 fork 耗时，单位为微秒；
- `INFO persistence` 查看 rdb_last_bgsave_time_sec；
- 系统内核参数 vm.overcommit_memory 必须为 1，否则 fork 可能直接失败；
- 检查透明大页是否关闭，THP 会显著放大 fork 延迟。

## 5. 磁盘写满处置
1. 现象：快照报错、AOF 无法追加、日志出现 No space left on device；
2. 临时回收：清理过期的 RDB 备份、切割日志、删除残留的临时文件；
3. 复核是否有日志或备份误写入数据目录；
4. 空间恢复后手动触发一次 `BGSAVE`，确认落盘状态回到 ok。

## 6. 内存碎片与大 key 排查
- 碎片率：`INFO memory` 中的 mem_fragmentation_ratio，1.0 至 1.5 为正常区间，持续高于 1.5 需要处理；
- 缓解手段：开启 activedefrag，或选择低峰期做主从切换；
- 大 key 扫描：`redis-cli --bigkeys` 列出各数据类型中最大的键，单键超过 10 万成员或 10MB 即按大 key 治理；
- 也可用 `redis-cli --memkeys` 按实际内存占用排序查看；
- 大 key 处置原则：拆分为多个小键，删除超大集合时避免直接 DEL（会阻塞主线程），改用 UNLINK 或分批处理。

## 7. 预防措施
- 为数据目录单独挂载分区，避免日志占满导致落盘失败；
- 监控项至少包含：used_memory_rss、mem_fragmentation_ratio、rdb_last_bgsave_status、aof_last_write_status；
- 每半年做一次备份可恢复性演练，在隔离环境用 dump.rdb 启动实例核对数据；
- 大 key 治理纳入开发规范：单键成员数上限 1 万。

## 8. 附录：INFO 关键字段速查
| 字段 | 所在段 | 判读标准 |
| --- | --- | --- |
| used_memory | memory | 与 maxmemory 对比 |
| mem_fragmentation_ratio | memory | 大于 1.5 需关注 |
| latest_fork_usec | stats | 大于 50 万即 500 毫秒，偏大 |
| rdb_last_bgsave_status | persistence | ok 为正常 |
| aof_last_bgrewrite_status | persistence | ok 为正常 |
