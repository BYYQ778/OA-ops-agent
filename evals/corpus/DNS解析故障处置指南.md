# DNS解析故障处置指南

## 概述
本指南覆盖内网域名无法解析、解析到错误地址、解析时快时慢三类问题的定位与恢复。解析问题影响链条长，建议按「客户端、链路、服务器、记录」四段逐步收敛。内网域名统一使用 svc.internal 后缀，DNS 服务器主备两台：主 10.20.30.11、备 10.20.30.12，所有客户端必须同时配置两台地址。

## 常见现象
- 浏览器提示 DNS_PROBE_FINISHED_NXDOMAIN 或无法找到服务器；
- 命令行 ping oa-app-01.svc.internal 报 Name or service not known；
- 同一域名在办公区解析正常，在机房却解析到已下线的旧地址；
- 应用日志出现 UnknownHostException，重启后偶发恢复。

## 排查步骤
### 指定服务器验证
先绕过本机配置，直接向内网 DNS 查询：nslookup oa-app-01.svc.internal 10.20.30.11；dig 环境用 dig @10.20.30.11 oa-app-01.svc.internal +short。若指定服务器能解析、默认查询失败，问题在本机 DNS 配置或缓存。

### 配置文件与缓存检查
Linux 依次检查 /etc/resolv.conf 的 nameserver 顺序与 /etc/hosts 内容；Windows 检查 C:\Windows\System32\drivers\etc\hosts 及 ipconfig /all 中的 DNS 服务器。刷新缓存：Windows 用 ipconfig /flushdns；使用 systemd-resolved 的发行版执行 resolvectl flush-caches；仍使用 nscd 的老系统执行 systemctl restart nscd。

### 委派链路追踪
需要确认委派链路时执行 dig +trace oa-app-01.svc.internal，逐步观察每一步返回；返回 SERVFAIL 多为上游服务器故障或 DNSSEC 校验失败。

## 解析超时与错误 IP 的定位
- 间歇超时：查看 /etc/resolv.conf 的 options 行，建议配置 options timeout:1 attempts:2 rotate，单次超时超过 2 秒基本可判定 DNS 侧问题；
- 解析到错误 IP：先确认客户端 hosts 是否有残留记录，再检查 DNS 服务器上是否存在同名 A 记录新旧并存，确认后清理旧记录并降低 TTL；
- 返回 NXDOMAIN：域名或记录不存在，核对命名规范与区域文件是否漏配。

## 内网域名与解析优先级
域名命名遵循 <用途>-<编号>.svc.internal 规范，例如 oa-app-01、db-mysql-01。解析优先级为：hosts 文件高于本地缓存、本地缓存高于 DNS 服务器。hosts 仅用于故障临时绕过，使用期不超过 48 小时，并登记到网络组登记表。

## 记录变更与同步
新增或修改解析记录按变更流程执行：变更前降低 TTL，双人核对域名与目标地址，变更后分别在主、备服务器上用 dig 直查验证，并通知常用方在业务低峰复测。记录变更模板包含域名、记录类型、目标地址、生效时间与操作人。

## 常见原因速查
- 客户端只配了一台 DNS 且恰好故障，或仍指向已下线的旧服务器；
- 区域文件修改后未同步到备服务器，主备解析结果不一致；
- 记录变更后旧值未清理，新旧 A 记录并存被轮询返回；
- 本机 hosts 残留调试记录，优先级高于 DNS 解析；
- 容器与宿主机存在两套解析配置，修改后互相覆盖。

## 检查清单
- [ ] 主机名与完整域名拼写已核对；
- [ ] 两台 DNS 服务器分别直查验证；
- [ ] hosts 与 resolv.conf 内容已确认；
- [ ] 客户端缓存刷新后复测通过；
- [ ] 相关变更已登记。

## 注意事项
- 直接编辑 /etc/resolv.conf 的改动在 NetworkManager 重启后会被覆盖，应修改网卡配置文件的 DNS1/DNS2 或用 nmcli 下发。
- 修改 DNS 记录前确认影响范围；常规 TTL 维持 300 秒，紧急切换时先降 TTL 再改记录。
- 禁止在生产服务器长期保留 hosts 临时记录，用完 48 小时内清理。
- 主备 DNS 服务器记录变更后 10 分钟内完成一致性核对；
- 排查过程保留 dig 输出作为证据，便于与网络组对账；
- 切换 DNS 前先在少量终端验证返回结果再全量下发。

## 附录：常用命令速查
| 场景 | 命令 |
|---|---|
| 指定服务器查询 | nslookup oa-app-01.svc.internal 10.20.30.11 |
| 简洁输出 | dig @10.20.30.11 oa-app-01.svc.internal +short |
| 委派追踪 | dig +trace oa-app-01.svc.internal |
| 刷新缓存（Linux） | resolvectl flush-caches |
| 刷新缓存（Windows） | ipconfig /flushdns |
| 查看解析配置 | cat /etc/resolv.conf |
