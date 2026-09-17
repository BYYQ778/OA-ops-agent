# OA 门户 Tomcat 服务故障排查手册

## 概述

门户应用部署在两个节点上，使用 Tomcat 9，实例根目录为 /opt/tomcat，监听 8080，由 systemd 托管，服务名为 tomcat。改动 server.xml 与 setenv.sh 前必须先备份。
门户节点采用双节点同时在线、接入层轮询分发的部署方式，任何一台节点维护都能由另一台承接全部流量，因此动手前务必先确认另一台节点健康。

## 故障现象

- 门户白屏或提示无法访问，接入层返回 502；
- 主机上进程消失，端口 8080 无监听；
- 日志出现 SEVERE、Address already in use、OutOfMemoryError 等关键字；
- 接入层监控出现 5xx 比例抬升，健康检查探测连续失败；
- 会话相关警告增多，用户被反复踢出。

## 排查步骤

1. 进程检查：执行 ps -ef | grep tomcat，或用 systemctl status tomcat 确认 Active 状态；
2. 端口检查：执行 ss -lntp | grep 8080，确认监听与对应 PID；
3. 日志检查：tail -n 200 /opt/tomcat/logs/catalina.out，配合 grep -n "SEVERE" 定位错误段；
4. 本机验证：curl -I http://127.0.0.1:8080/portal/ 排除接入层干扰；
5. 资源检查：free -m、df -h 确认内存与磁盘水位；
6. 会话检查：登录高峰期间在管理页面核对在线会话总数与上限的差距。

## 常见原因

| 现象 | 可能原因 | 确认方式 |
|---|---|---|
| 启动即退出 | 端口 8080 被占用 | ss -lntp 查占用进程 |
| 启动报内存错误 | -Xmx 设置超过物理内存 | 检查 setenv.sh 与 free -m |
| 日志无法写入 | 日志目录属主或权限错误 | ls -l /opt/tomcat/logs |
| 配置报错 | server.xml、context.xml 被误改 | 对比最近一次备份 |
| 运行中异常退出 | 被 OOM Killer 终止 | dmesg 中查 oom-killer 记录 |
| 新用户无法登录 | 会话数达到上限 | 日志中 session 相关警告 |

- 端口占用最常见于残留进程或同机部署的其它服务，确认后释放端口再启动；
- 内存参数：当前配置为 -Xmx4096m，若物理内存较小，启动阶段就会失败；
- 权限问题多发生在以 root 启动过后，日志文件属主变为 root，运行用户无法继续写入；
- 配置错误重点核对 Connector 端口、AJP 是否启用、数据源地址；
- 定时任务堆积：报表导出与流程补偿在整点集中执行，线程占用升高导致新请求排队；
- 会话数满时表现为老用户正常、新用户登录卡住，需要及时扩容或调大会话上限。

## 处置与恢复

标准停止使用 systemctl stop tomcat；缓慢关闭不生效时，可等待 30 秒后按 PID 强制结束，强制前确认没有正在处理的流程实例。启动使用 systemctl start tomcat，随后跟踪日志，出现 Server startup in 字样即视为启动完成；再用 curl 验证返回 200 后，从接入层把流量挂回。
若短时间内无法定位根因，先把节点从接入层摘除，保留现场再进行二线排查；恢复后 30 分钟内保持跟踪，确认错误日志不再新增。

## 预防措施

- setenv.sh 固定为 -Xms4096m -Xmx4096m，并追加 -XX:+HeapDumpOnOutOfMemoryError；
- 日志按天切割，catalina.out 保留 7 天，避免单文件无限增长；
- 会话超时设置为 30 分钟，登录高峰前检查会话总数；
- 每半年复核一次 JVM 参数与并发配置，结合容量评估决定是否增加节点；
- 每季度做一次主备切换演练，确认单节点可承载全部流量；
- 对 server.xml 与 setenv.sh 建立版本备份，每次修改前先比对差异再落盘。

## 附录

- 关键路径：实例根目录 /opt/tomcat；日志目录 /opt/tomcat/logs；启动脚本 /opt/tomcat/bin/startup.sh；配置目录 /opt/tomcat/conf。
- 检查顺序：进程、端口、日志、本机接口、资源。
- 端口核查使用 ss -lntp 确认 8080 的监听状态与进程 PID。
- 常用巡检命令：ps -ef 看进程；ss -lntp 看端口；free -m 看内存；df -h 看分区；jstat -gcutil 看回收情况。
- 变更提示：修改 server.xml 后先做配置检查再重启，禁止带病重启。
