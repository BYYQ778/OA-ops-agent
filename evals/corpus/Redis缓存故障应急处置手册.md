# Redis缓存故障应急处置手册

**适用对象**：oa-cache-01（10.20.30.21，主）、oa-cache-02（10.20.30.22，从），Redis 6.2，端口 6379。
**关键路径**：配置 /etc/redis/redis.conf，日志 /var/log/redis/redis-server.log，数据 /var/lib/redis。

## 1. 概述
Redis 承载 OA 的登录会话、验证码与热点字典缓存。缓存层故障时，业务侧表现为登录态大面积失效、页面数据回退到数据库导致整体变慢。应急处置原则是：先恢复可用性，再定位根因，切勿在业务报障高峰做深度分析。

## 2. 连接失败排查
1. 确认进程状态：`systemctl status redis`；
2. 检查端口监听：`ss -lntp | grep 6379`；
3. 本地连通性：`redis-cli -h 127.0.0.1 -p 6379 ping`，正常应返回 PONG；
4. 若返回 Connection refused，多为服务未启动或 bind 配置与访问来源不匹配；
5. 客户端报 NOAUTH Authentication required，说明未携带密码，核对 requirepass 与业务配置是否一致；
6. 客户端报 LOADING Redis is loading the dataset in memory，说明实例正在加载 RDB，等待完成即可，反复重启只会拉长恢复时间。

## 3. OOM 内存告警处置
写入命令返回 `(error) OOM command not allowed when used memory > 'maxmemory'` 时，按下表逐项排查：

| 排查项 | 命令 | 说明 |
| --- | --- | --- |
| 内存用量 | INFO memory | 对比 used_memory 与 maxmemory |
| 淘汰策略 | CONFIG GET maxmemory-policy | noeviction 会直接拒绝写入 |
| 大 key | redis-cli --bigkeys | 定位异常膨胀的键 |

处置手段：临时提高 maxmemory，或将淘汰策略调整为 allkeys-lru 或 volatile-lru；确认业务可接受淘汰后再执行 CONFIG SET，并在处置结束后把参数写回配置文件，防止下次重启后失效。

## 4. 主从状态检查
执行 `INFO replication`，重点字段：role、connected_slaves、master_link_status（up 或 down）、master_last_io_seconds_ago。链路断开时从库数据会持续落后，应先排查网络与主库负载，再决定是否执行 SLAVEOF NO ONE 将从库提升为主库。

## 5. 重启与数据恢复注意事项
1. 重启前确认最近一次落盘时间（LASTSAVE 返回值）；
2. 主从架构下先重启从库验证，再操作主库；
3. 使用哨兵或集群时，重启前先摘除节点，避免误触发主从切换；
4. 重启后观察日志中的加载完成记录，确认数据量符合预期；
5. 会话类数据可容忍丢失时，不要为了保全数据拖延恢复时间。

## 6. 常用诊断命令
- `redis-cli INFO memory` 查看内存全景；
- `redis-cli CLIENT LIST` 关注 addr、age、idle 明显异常的长连接；
- `redis-cli --latency` 观察响应延迟变化；
- `redis-cli INFO stats | grep rejected_connections` 查看是否出现连接被拒；
- `redis-cli DBSIZE` 查看当前库键总量。

## 7. 预防措施
- maxmemory 建议设置为物理内存的 60% 至 70%，并为复制缓冲区预留空间；
- 会话键必须设置 TTL，禁止写入无过期时间的会话数据；
- 每季度核查一次大 key 与慢命令记录（slowlog get 128）；
- 主从部署必须开启持久化，且从库保留 RDB，禁止全部节点纯内存运行。

## 8. 附录：常见报错对照
| 报错信息 | 主要原因 | 处置方向 |
| --- | --- | --- |
| Connection refused | 服务未启动或 bind 配置错误 | 启动服务并核对配置 |
| OOM command not allowed | 内存超限且无淘汰策略 | 调整 maxmemory-policy |
| LOADING Redis is loading | 启动加载数据文件中 | 等待加载完成 |
| READONLY You can not write | 连接到了只读从库 | 将连接切回主库 |
