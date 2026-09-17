# MySQL数据库故障排查手册

**适用范围**：公司内部 OA 平台 MySQL 5.7 / 8.0 实例（单机与主从架构），主库 db-oa-01（10.20.30.11），从库 db-oa-02（10.20.30.12）。
**关键路径**：数据目录 /var/lib/mysql，配置文件 /etc/mysql/my.cnf，服务名 mysqld。

## 1. 概述
OA 平台的用户中心、流程引擎与消息表均落在 MySQL 上。业务侧反馈"页面打不开、单据提交无响应"时，值班人员应先按本手册确认数据库层状态，再决定是否转交应用组排查。数据库层故障的典型表征是：应用日志出现 SQLException、连接超时或 500 错误码，而服务器本机 CPU、内存未必明显异常。

## 2. 服务启停与状态检查
1. 查看状态：`systemctl status mysqld`，确认 Active 为 active (running)，且末行无 failed 字样；
2. 启动：`systemctl start mysqld`；停止：`systemctl stop mysqld`；重启：`systemctl restart mysqld`；
3. 确认端口监听：`ss -lntp | grep 3306`；
4. 本地登录验证：`mysql -uroot -p -e "SELECT VERSION(), NOW();"`；
5. 开机自启检查：`systemctl is-enabled mysqld`，期望输出 enabled。

## 3. 错误日志排查
错误日志默认位于 /var/log/mysql/error.log。执行 `tail -n 200 /var/log/mysql/error.log` 后，优先关注下表关键字：

| 日志关键字 | 含义 | 处置方向 |
| --- | --- | --- |
| InnoDB: Database page corruption | 数据页损坏 | 单表检查后修复，必要时从备份恢复 |
| Too many connections | 连接数达到上限 | 临时调大上限并排查连接泄漏 |
| Access denied for user | 账号密码或授权问题 | 核对账号、来源主机与密码 |
| Disk is full | 磁盘空间耗尽 | 清理 binlog 与临时文件并扩容 |
| Aborted connection | 客户端异常断开 | 检查网络抖动与连接池配置 |

## 4. 连接失败类故障
客户端报错与服务端原因的对照如下：
- 1045 Access denied：账号或密码错误，或该账号不允许从当前主机登录，应检查 mysql.user 表的 host 字段；
- 1040 Too many connections：当前连接数已达 max_connections，新连接会被直接拒绝。可先用 `mysqladmin -uroot -p processlist` 观察，再临时提高上限；
- 2003 Can't connect to MySQL server：网络不通、端口未监听或防火墙拦截，先用 ping 与 `telnet 10.20.30.11 3306` 验证；
- 2002 本地 socket 不存在：多为服务未启动或 socket 路径被改动。

## 5. 表损坏检查与修复
怀疑某张表损坏（查询报 Table is marked as crashed）时按以下顺序处理：
1. 检查：`CHECK TABLE oa_flow.t_flow_task;`
2. MyISAM 表可直接 `REPAIR TABLE oa_flow.t_flow_task;`
3. InnoDB 表优先执行 `ALTER TABLE oa_flow.t_flow_task ENGINE=InnoDB;` 触发重建；
4. 严重损坏时用 innodb_force_recovery 逐级（1 到 6）挂载后导出数据，切勿长期以 6 运行。

## 6. 常见原因汇总
| 现象 | 常见原因 | 责任方 |
| --- | --- | --- |
| 连接全满 | 连接池未回收、慢 SQL 堆积 | 应用 / DBA |
| 主从延迟增大 | 大事务、从库回放能力不足 | DBA |
| 服务反复重启 | 内存不足被系统杀掉、磁盘写满 | 系统 / DBA |

## 7. 标准处置顺序
1. 确认影响面：涉及哪些业务、多少用户；
2. 保留现场：SHOW PROCESSLIST 结果与错误日志片段先行归档；
3. 能快速恢复的先恢复（如重启释放连接），再分析根因；
4. 涉及数据损坏的，务必先做物理备份（cp -a 数据目录或 xtrabackup）再动手；
5. 处置完成后在值班群同步结果并登记工单。

## 8. 预防措施
- 每季度复查一次连接池回收参数与 max_connections 是否匹配；
- 禁止 root 账号远程登录，应用一律使用最小权限独立账号；
- 监控至少覆盖：连接数、主从延迟、磁盘使用率、InnoDB 等待事件；
- 每年组织一次主从切换演练，确保从库可独立接管业务。

## 9. 附录：常用命令速查
- 当前连接数：`SHOW STATUS LIKE 'Threads_connected';`
- 连接数上限：`SHOW VARIABLES LIKE 'max_connections';`
- 会话明细：`SHOW FULL PROCESSLIST;`
- 主从状态：`SHOW SLAVE STATUS\G`
- 逻辑备份：`mysqldump -uroot -p --single-transaction --master-data=2 oa_flow > oa_flow_$(date +%F).sql`
