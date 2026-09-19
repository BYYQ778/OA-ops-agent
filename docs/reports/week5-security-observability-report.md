# 第 5 周：可观测性与安全——交付报告（feat/observability-security）

> 口径声明：本文所有数据来自 **2026-09-18 本机实测**（worktree `oa-ops-agent-observability-security`，
> 基于第 4 周尖端 `f250434`）。运行验证使用真实服务（严格模式通过 `OA_AUTH_BYPASS_LOOPBACK=0`
> 让 localhost 复现远端行为）；OTel SDK 为**临时安装于运行环境 env_new 的可选依赖**，未列入
> 项目依赖；无生产数据、无未经测量的断言。

## 一、验收对照（执行记录第 5 条）

| 验收项 | 状态 | 证据 |
|---|---|---|
| 本地结构化日志 | ✅ | JSON Lines（`OA_LOG_FORMAT=json` / `logging.format`）；实测 14 行 JSON 日志、11 行携带 `request_id`（见 §三.3） |
| OpenTelemetry | ✅（可选依赖） | 真 SDK 实测：`http.request` + `rag.query` span 导出（console exporter）+ 12 条日志带 `trace_id`（§三.4）；无 SDK 时自动降级 no-op |
| Prometheus | ✅（零新依赖实现） | `GET /api/v1/metrics`：保留旧契约行 + 新增 `oa_http_requests_total{method,route,status}`、`oa_http_request_duration_seconds` 直方图（le 桶 + `_sum`/`_count`）、`oa_build_info`、`oa_process_start_time_seconds` |
| 可选 Langfuse | ✅（配方） | 追踪 exporter 可选 OTLP（endpoint/headers 可配，可指向 Langfuse 的 OTel 摄入端点）；**真实 Langfuse 链路未实测**（如实声明，见 §五） |
| Session/RBAC | ✅ | 登录/登出/会话自述 + `oa_session` cookie（HttpOnly/SameSite=Lax/TTL 可配）+ 角色 admin/viewer + 路由策略表；严格模式 E2E 15/15（§三.1） |
| 密钥隔离 | ✅ | 日志脱敏 filter（环境中 `*PASSWORD*/*API_KEY*/*TOKEN*` 值 ≥8 字符出现在日志即替换 `***`）；`.env.example` 去掉明文默认密码；config.yaml 清除 `admin123` 默认值；启动门禁 |
| 桌面启动方式兼容 | ✅ | 回环旁路：默认模式（:7864）与 `desktop_app.py --backend`（:7860）匿名访问全部 200（本机免登录，桌面壳无感）（§三.5） |
| 未完成鉴权前不得公开部署 | ✅ | 启动门禁实测：`--host 0.0.0.0` + 空密码 → **拒绝启动**（exit 1 + 修复指引）（§三.2） |

## 二、交付清单

**新增模块（纯标准库，零新硬依赖）**

| 模块 | 职责 |
|---|---|
| `utils/request_context.py` | request-id / trace-id 的 contextvars 贯通（日志自动携带） |
| `utils/tracing.py` | OTel 薄封装：无 SDK/未启用 = no-op；console（立即输出）/ otlp（批量）exporter；span 期间 trace_id 写入日志上下文 |
| `utils/security.py` | PBKDF2-HMAC-SHA256 哈希（20 万迭代）+ 恒时比较 + 会话令牌（服务端只存 sha256）+ 登录限速（滑动窗口）+ **启动安全门禁** + `OA_AUTH_*` 环境变量开关 |
| `ui/routers/auth.py` | `POST /api/auth/login`（cookie/TTL/429/审计）、`POST /api/auth/logout`、`GET /api/auth/session`（loopback/session/disabled/anonymous 自述） |
| `ui/templates/login.html` | 登录页（深浅一致风格、`?next=` 回跳、错误/限速提示） |
| `scripts/hash_password.py` | 生成 `password_hash`（推荐替代明文配置） |

**升级模块**

| 模块 | 变更 |
|---|---|
| `utils/logger.py` | JSON Lines formatter + `logging.format` 开关（env `OA_LOG_FORMAT` 可覆盖）+ 密钥脱敏 filter；text 默认格式零变化 |
| `utils/metrics.py` | 手写 Prometheus 原语（counter/gauge/histogram + `le` 桶 + `TYPE/HELP` 行，标签转义，路由基数归一化）；第 4 周 API（inc/get/reset/snapshot/render）与渲染行**零变化** |
| `ui/middleware.py` | 纯 ASGI 三件套：`RequestContextMiddleware`（request-id + 访问日志 + HTTP span）、`MetricsMiddleware`（请求计数/时长）、`AuthGateMiddleware`（豁免表 + 角色策略 + 401/403/302） |
| `utils/database.py` | 新表 `sessions`（只存 token 哈希）与 `auth_events`（登录审计）+ 存取方法 |
| `ui/server.py` | 三中间件接线（顺序：请求上下文 → 指标 → 认证门 → 路由）+ `run_server` 启动门禁 + `/login` 路由 + `auth_router_factory` 注入点 |
| `ui/routers/incidents.py`、`agents/incident_kb.py` | `incident.analyze` / `rag.query` / `kb_search` span 埋点 |
| `ui/templates/index.html` | 侧栏会话控件（session 模式显示用户名/角色/登出）+ 401 兜底跳转（tag `ui-before-security` 可回退） |
| `config.yaml` / `.env.example` | auth 段重写（bypass_loopback / session_ttl / max_attempts / users 示例）、`logging.format`、`observability.tracing`；默认密码清除；环境变量说明补齐 |
| `.github/workflows/quality.yml` | docker-core 冒烟补 `OA_AUTH_PASSWORD`（启动门禁生效后必需） |

**RBAC 策略（`ui/middleware.py` 策略表，默认 GET=viewer、写=admin）**

- viewer 白名单（只读查询/诊断类 POST）：`/api/log/*`、`/api/net/*`、`/api/ssl/*`、`/api/sec/*`、`/api/db/*`、`/api/kg/*`（查询）、kb 问答系列、`/api/v1/rag/query`、`/api/v1/incidents/analyze`；
- admin（其余写操作）：巡检运行/调度（`/api/inspect/*`）、`/api/config/save`、知识库导入/删除/清空等；
- 豁免（公开）：`/api/health`、`/api/dev/version`、`/api/auth/*`、`/login`、`/static/*`、`/docs` 等。

## 三、实测证据（2026-09-18）

**1. 严格模式 RBAC E2E（真实服务 :7865，15/15）**

| 用例 | 结果 |
|---|---|
| 匿名 GET `/api/v1/incidents` | 401 ✅ |
| admin 登录 + `POST /api/inspect/stop` | 200 放行 ✅ |
| viewer 登录 + `POST /api/inspect/stop` | **403 拒绝** ✅ |
| viewer GET / 白名单 POST（rag/query） | 200 ✅ |
| 指标：新计数器/直方图/build_info + 旧契约行 | 全命中 ✅ |
| 审计表：login_ok / login_fail / logout / request_id | 全命中 ✅ |

**2. 启动门禁**：`main.py --host 0.0.0.0`（无密码）→
`[安全门禁] 拒绝启动：用户 admin 密码为空；非回环绑定必须先设置强密码…` + `exit_code=1` ✅

**3. JSON 日志**：14 行 JSON、11 行含 `request_id`；字段集 `{ts, level, logger, msg, request_id}` ✅

**4. OTel（env_new 临时安装 opentelemetry-sdk）**：导出的 span 名称 `['http.request', 'rag.query']`；
日志 `trace_id` 出现 12 次（32 位 hex，与 span 对应）✅

**5. 桌面兼容**：默认模式 :7864 与 `desktop_app.py --backend` :7860 —— health/session(loopback)/index/incidents
匿名全部 200（本机回环免登录）✅

**6. 质量门槛**：927 passed（857 基线 + 70 新增）；ruff 严格范围全绿；pyright 全范围 0 错误；
Playwright 登录流（跳转→报错→登录→会话控件→登出）全过 + GLM-4V 目检通过 ✅

## 四、设计决策与取舍

1. **零新硬依赖**：指标用手写原语而非 `prometheus_client`——绿色版冻结产物（第 6 周重建）不含新包时
   导入即崩，手写实现保持零风险且格式可逐行断言；OTel 按官方接口做可选封装（无 SDK 自动 no-op），
   符合其"instrumentation 可降级"惯例。
2. **回环旁路语义**（`auth.bypass_loopback` 默认开）：桌面壳与同机浏览器是单人工具场景的"机主"，
   免登录保证桌面兼容（验收硬性要求）；**远端客户端一律要求登录**；共享工作站可关旁路（env 可覆盖）。
3. **启动门禁而非文档承诺**：把「未完成鉴权前不得公开部署」做成代码级拒绝（非回环 + 弱配置 → exit 1），
   配置撒谎问题从根上关闭（优化清单 P0-5）。
4. **中间件顺序经过探针实测**（starlette `add_middleware` 后加的更外层），401/403 响应仍带
   request-id 并计入指标（服务可观测性不因拒绝而丢失）。
5. **`/docs`、`/redoc`、`/openapi.json` 保持公开**：便于 API 使用与面试演示；如部署公网想收回，
   在豁免表移除即可（一行改动）。列入残余风险。

## 五、残余风险与后续

1. **OTLP/Langfuse 真实链路未实测**（console exporter 已验证；OTLP 需安装
   `opentelemetry-exporter-otlp-proto-http` 后再验证一次端到端导出）；
2. `/docs` 系列公开（见 §四.5），公网部署前建议收回或加鉴权；
3. HTTPS 部署需设 `auth.cookie_secure: true`（配置项已支持）；
4. 绿色版冻结产物未在本周重建——新模块为纯标准库，预期兼容，**第 6 周重建时需回归一次**；
5. CI docker-core 冒烟已补密码通过门禁，远端生效随推送验证。

## 六、提交链（feat/observability-security，基于 f250434）

```
c5d92ad 计划 → 0a5fea1 日志 → 8c06adc 指标 → 20a5cb3 追踪 → 7beb684 安全基础
→ 4d0a536 认证端点 → 78766f4 RBAC 门 → 5214151 前端（tag ui-before-security）
→ 本文档（文档同步）
```

**已推送（2026-09-18，ssh-origin 直通）**；远端 CI 双绿：run `35318040465`
（quality **927 passed / TOTAL 81%**——覆盖率 80%→81%；docker-core 冒烟通过，容器已按
启动门禁要求注入 `OA_AUTH_PASSWORD`）；PR **#5**（→ feat/incident-rca，堆叠链之一）。

回退：改动全部在本 worktree/分支；前端另有 tag `ui-before-security`；回退 = `git checkout f250434`。
