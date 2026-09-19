# 安全设计（docs/SECURITY.md）

> 原则：**本地优先、默认安全、说到做到**——安全约束尽量落成代码门禁（启动拒绝、模型层校验），而不是文档承诺。
> 本文概述当前实现；完整实测记录见 [reports/week5-security-observability-report.md](reports/week5-security-observability-report.md)。

## 一、威胁模型与信任边界

- 定位：单人 / 内网运维工具。桌面版与同机浏览器（回环访问）视为「机主」。
- 凡**非回环**客户端（局域网/公网）默认不信任：必须登录，并受 RBAC 约束。
- 拿不到安全的配置（非回环 + 弱密码）时**拒绝启动**，从根上避免「配置撒谎」。

## 二、认证与会话

| 项 | 实现 |
|---|---|
| 密码存储 | PBKDF2-HMAC-SHA256（20 万次迭代）+ 恒时比较；`config.yaml` 只存哈希（`scripts/hash_password.py` 生成） |
| 会话 | 登录成功下发 `oa_session` cookie（HttpOnly / SameSite=Lax / TTL 可配 / 可选 `Secure`）；**服务端只存 token 的 sha256 哈希**，登出即失效 |
| 登录限速 | 滑动窗口（超限 429），上限可配（`auth.max_attempts`） |
| 审计 | 登录成功 / 失败 / 登出写入 `auth_events` 表（含来源 IP 与时间） |
| 多用户 | `auth.users` 配置多账号 + 角色（`admin` / `viewer`） |

## 三、RBAC 策略（默认：GET=viewer、写=admin）

| 类别 | 范围 |
|---|---|
| 豁免（无需登录） | `/api/health`、`/api/dev/version`、`/api/auth/*`、`/login`、`/static/*`、`/docs` 等 |
| viewer 可用（只读 + 查询类 POST） | `/api/log/*`、`/api/net/*`、`/api/ssl/*`、`/api/sec/*`、`/api/db/*`、`/api/kg/*` 查询、知识库问答系列、`/api/v1/rag/query`、`/api/v1/incidents/analyze` |
| admin（写操作） | 巡检运行/调度（`/api/inspect/*`）、`/api/config/save`、知识库导入/删除/清空、会话删除/重命名等 |

## 四、回环旁路（桌面兼容）

- 默认 `auth.bypass_loopback: true`：本机回环访问免登录，保证桌面版 / 绿色版双击即用。
- 远端客户端**一律要求登录**；共享工作站可关闭旁路。
- 复现「严格模式」（让 localhost 也走完整认证）：设置 `OA_AUTH_BYPASS_LOOPBACK=0`。

## 五、启动安全门禁（代码级）

- 绑定非回环地址（如 `0.0.0.0`）且密码为空 / 默认值时：**拒绝启动**（exit 1 + 修复指引）。
- 实测证据（2026-09-18）：`main.py --host 0.0.0.0`（无密码）→ `[安全门禁] 拒绝启动：…非回环绑定必须先设置强密码…`。
- CI docker 冒烟按门禁要求注入非默认 `OA_AUTH_PASSWORD`。

## 六、密钥与敏感信息

- 密钥**只允许**放在 `.env`（已 gitignore）；`config.yaml` 只能用 `${VAR:}` 占位符引用，仓库内不含明文密钥。
- `.env.example` 全部为占位符（历史版本曾存在的明文默认密码已清除）。
- 日志脱敏过滤器：环境变量中 `*PASSWORD*` / `*API_KEY*` / `*TOKEN*` 的值出现在日志时替换为 `***`。
- 密码不进入日志、Prompt、历史记录与异常响应；数据库连接密码按需使用强类型对象。

## 七、注入与破坏面防护

| 面 | 措施 |
|---|---|
| 提示词注入 | `utils/prompt_safety`（第 1 周加入）+ 工具白名单 / 参数校验 / 输出验证；恶意文档无法触发工具执行 |
| 命令注入 | 本地执行参数化（`shell=False`，数据库/网络/巡检执行器全覆盖）；SSH/巡检命令白名单 |
| 路径穿越 | 上传 / 文档访问路径规范化校验，限制在 data 目录内 |
| 网络探测 SSRF 边界 | 按工具定位允许内网/回环探测；云元数据（169.254.169.254 等）与链路本地地址硬阻断；HTTP 检查重定向逐跳校验（≤3 跳）、TLS 证书默认校验（`network_diag.tls_verify`，自签场景可显式关闭并告警） |
| 危险动作 | 根因诊断**只读**：只给分析和建议，不执行任何修复命令（模型层强制） |
| 日志伪造命令 | 日志内容只作为文本分析对象，永远不会被当作命令执行 |

## 八、可观测性与审计

- `request-id` 贯通（响应头 + 日志 + 指标）；`OA_LOG_FORMAT=json` 输出 JSON Lines。
- 审计事件表（`auth_events`）+ Prometheus 指标（`/api/v1/metrics`）；OpenTelemetry 追踪可选启用（无 SDK 自动降级）。

## 九、部署基线

- **公网 / 局域网部署检查清单**：
  1. `OA_AUTH_PASSWORD` 设置强密码（或 `auth.users` + 哈希）；2. HTTPS 反代 + `auth.cookie_secure: true`；
  3. 视需要关闭回环旁路（`auth.bypass_loopback: false`）；4. 防火墙只放行必要端口；5. 定期备份 `data/`。
- `/docs`、`/openapi.json` 默认公开（便于集成与演示）；公网部署如需收回，从豁免表移除即可（一行改动）。

## 十、残余风险与路线（诚实声明）

1. OTLP / Langfuse 真实导出链路未实测（console exporter 已实测；安装 `opentelemetry-exporter-otlp-proto-http` 后可端到端验证）；
2. `/docs` 系列默认公开（见上节）；
3. HTTPS 需自行配置反代（项目只提供 `cookie_secure` 开关）；
4. 认证体系为轻量自研实现（Session+RBAC），面向内网工具场景；面向大规模多租户场景应改用成熟网关/IdP。
5. 数据库 CLI 客户端（mysql/sqlcmd/sqlplus/redis-cli）密码会出现在本机进程命令行（进程列表可见）；彻底规避需更换原生驱动（v3.1+ 候选）。
6. HTTP 健康检查为「先解析后请求」，存在 DNS 重绑定类 TOCTOU 的理论窗口；内网工具场景接受该风险，公网部署建议经反向代理隔离。

## 附：验证证据摘要（2026-09-18 实测）

| 验证 | 结果 |
|---|---|
| 严格模式 RBAC E2E（真实服务） | 15/15：匿名 401、viewer 写操作 403、admin 放行、白名单放行、审计四类事件、指标新旧契约 |
| 启动门禁 | 非回环 + 空密码 → 拒绝启动（exit 1） |
| JSON 日志 | 14 行 JSON、11 行含 request-id；字段 `{ts, level, logger, msg, request_id}` |
| OTel 真 SDK | span `[http.request, rag.query]` 导出 + 12 条日志携带 trace_id |
| 密钥隔离 | 脱敏 filter 生效；`.env.example` 无明文默认密码 |
| 桌面兼容 | 默认模式与 `--backend` 回环匿名全通（免登录） |

## 附：v3.0.1 安全修复验证（2026-09-19）

针对 v3.0.0 的第三方安全复审（6 项）逐条修复并补测试：数据库执行器与本机巡检全部参数化（`shell=False` + 注入回归）；会话删除/重命名归 admin（viewer 403 反向测试）；HTTP 检查 SSRF 硬阻断 + 重定向逐跳校验 + TLS 默认校验；SSH 默认 `RejectPolicy` + known_hosts；版本与依赖统一（`pypdf`）。

| 验证 | 结果 |
|---|---|
| 全量 pytest（离线） | **945 passed**（含 18 条安全新增用例） |
| Ruff（全量 + 严格范围）/ Pyright / compileall / git diff --check | 全绿（Pyright 0 errors） |
| uv lock 一致性 | `uv lock --check`（默认索引）exit 0 |
