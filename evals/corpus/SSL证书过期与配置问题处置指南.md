# SSL证书过期与配置问题处置指南

## 概述
本指南用于处置 HTTPS 访问中与证书相关的报错，包括证书过期、域名不匹配、证书链不完整、自签证书引发的浏览器拦截四类问题。证书类问题多与时间、链路配置相关，先核对报错文本再动手替换，避免误换正常的证书。对外站点证书清单由运维组统一登记，到期前 30 天进入续期流程，逾期未续按变更事故追责。

## 常见报错识别
| 报错文本 | 含义 | 处置方向 |
|---|---|---|
| certificate has expired | 证书已过期 | 立即续期并替换 |
| certificate does not match | SAN 缺少访问域名 | 补全 SAN 后重新签发 |
| self signed certificate | 使用了自签证书 | 更换受信任证书或导入根证书 |
| unable to get local issuer certificate | 中间证书缺失 | 配置全链证书文件 |
| SSL_ERROR_RX_RECORD_TOO_LONG | 用 https 访问了 http 端口 | 核对接入端口与协议 |

## 查看证书状态
用 openssl s_client 直接读取线上证书：echo | openssl s_client -connect oa.example.com:443 -servername oa.example.com 2>/dev/null | openssl x509 -noout -dates -subject -issuer。输出中 notAfter 为到期时间，剩余不足 30 天启动续期；notBefore 晚于当前时间说明服务器时钟异常，先修时间再处理证书。

## 续期与替换流程
1. 生成新密钥与 CSR：openssl req -new -newkey rsa:2048 -nodes -keyout oa.example.com.key -out oa.example.com.csr；
2. 提交 CSR 至 CA 完成签发，将站点证书与中间证书合并为全链文件；
3. 替换前备份旧文件，再覆盖 /etc/nginx/ssl/ 目录下对应文件；
4. 执行 nginx -t 校验配置语法，通过后 nginx -s reload 平滑生效，禁止直接 kill 进程；
5. 用 curl -vI https://oa.example.com 复核无告警后，更新证书登记表。

## Nginx 配置要点
ssl_certificate 指向全链证书文件（站点证书在前、中间证书在后），ssl_certificate_key 指向私钥文件；ssl_protocols 建议只保留 TLSv1.2 与 TLSv1.3。证书与私钥权限设置为 600、属主 root，禁止放入 Web 根目录。多域名共用一张证书时，确保 server_name 与 SAN 完全一致，避免个别域名仍报错。

## 服务器时间同步
证书校验依赖系统时间，偏差超过 5 分钟将导致校验失败。Linux 执行 timedatectl 查看同步状态、chronyc sources -v 核对上游时间源；Windows 执行 w32tm /resync。时间未同步的机器不得直接替换证书，必须先修复时间源。

## 私钥泄露的紧急处置
确认私钥外泄后按紧急变更处理：先吊销旧证书，再签发并替换新证书，最后排查泄露途径并加固权限。吊销后核对旧序列号已进入吊销列表，通知依赖该证书的对接方更新信任配置，全过程记录时间线并归档吊销回执。

## 替换后的观察项
替换完成后持续观察 24 小时：接入层 4xx、5xx 比例，证书链校验结果，移动端与内网客户端的兼容性反馈。发现个别终端仍报错时，优先核对该终端信任库与系统时间，而不是重复替换证书。

## 检查清单
- [ ] 报错文本与证书状态已比对确认；
- [ ] 新证书 SAN 覆盖全部访问域名；
- [ ] 全链文件顺序正确、内容无缺失；
- [ ] nginx -t 通过且已 reload；
- [ ] 替换后 24 小时监控无异常波动。

## 注意事项
- 私钥泄露必须立即吊销并更换，吊销信息同步登记；
- 内网自签证书需将根证书导入客户端信任库，否则浏览器依旧拦截；
- 续期替换建议安排在前一天 22:00 之后执行，避开业务高峰。
- 证书替换后保留旧文件至少一个版本周期，回滚时可直接还原；
- 监控对到期时间设置提前 45 天提醒，避免节假日叠加导致逾期。

## 附录：自检命令速查
| 目的 | 命令 |
|---|---|
| 查看线上证书 | openssl s_client -connect oa.example.com:443 -servername oa.example.com |
| 查看证书文件有效期 | openssl x509 -in oa.example.com.crt -noout -dates |
| 查看证书全文 | openssl x509 -in oa.example.com.crt -noout -text |
| 校验 Nginx 语法 | nginx -t |
| 验证 HTTPS 访问 | curl -vI https://oa.example.com |
| 核对时间同步 | chronyc sources -v |
