# 第 5 周：可观测性与安全（feat/observability-security）

> 基线：第 4 周尖端 `f250434`（857 passed / TOTAL 80% / CI 双绿）；
> 工作树：`E:\YunweiAgent\oa-ops-agent-observability-security`。
> 验收口径（执行记录第 5 条）：**本地结构化日志、OpenTelemetry、Prometheus、可选 Langfuse、
> Session/RBAC、密钥隔离；桌面启动方式兼容；未完成鉴权前不得公开部署。**

## 一、现状盘点（2026-09-18 侦察结论）

| 维度 | 现状 | 缺口 |
|---|---|---|
| 日志 | `utils/logger.py` 纯文本、按天轮转（console INFO / file DEBUG） | 无 JSON、无 request-id、无密钥脱敏 |
| 指标 | `utils/metrics.py` 仅计数器；`GET /api/v1/metrics` 最小文本（第 4 周） | 无 histogram/gauge、无 HTTP 请求维度 |
| 追踪 | 无 | 无 OTel 集成 |
| 认证 | `config.yaml auth.enabled: true` 但**无任何中间件**（P0-5「配置撒谎」）；`.env.example` 明文 `admin123`；README 自述"暂未启用（v2.3）" | 需落地 Session/RBAC（P2-13）并关闭 P0-5 |
| 越权面 | `run_server`/`main.py --host` 可绑 0.0.0.0；Docker CMD `--host 0.0.0.0` | 无"非回环绑定 + 默认/空密码"门禁 |
| 桌面 | `desktop_app.py` 后端子进程 = loopback（`127.0.0.1`，`--backend`） | 认证必须回环旁路，否则桌面壳被登录页挡住 |
| 依赖 | 无 `prometheus_client` / `opentelemetry-*` | 本周边界：**不新增硬依赖**（见 D1/D3） |

保留契约（不得破坏）：

- `/api/health` 公开（CI docker 冒烟 / 桌面壳健康检查依赖）；
- `/api/v1/metrics` 既有行格式（`oa_incidents_analyze_total 1` 等子串断言在测试中）；
- legacy `/api/*`、`/api/kb/*` 行为在回环场景零变化；
- `utils/metrics.py` 的 `inc/get/reset/snapshot/render` API（测试直接引用）。

## 二、设计决策

- **D1 · 指标：手写完整 Prometheus 原语，零新依赖。** counter/gauge/histogram
  （`le` 桶 + `_sum`/`_count`，默认桶 `.005~10s`）+ `# TYPE`/`# HELP` 行，文本格式按规范渲染。
  理由：绿色版冻结产物（第 6 周重建）不含新第三方包时导入即崩；离线/CI 零风险；
  格式可被逐行断言测试。HTTP 埋点：`oa_http_requests_total{method,route,status}` +
  `oa_http_request_duration_seconds` 直方图；进程信息：`oa_build_info{version}`、
  `oa_process_start_time_seconds`。路由标签做基数归一（`/api/v1/incidents/{id}` 聚合，
  防 ID 爆炸），normalizer 独立可测。
- **D2 · 结构化日志。** 新增 `logging.format: text|json`（默认 text，向后兼容；
  支持 `OA_LOG_FORMAT` 环境变量覆盖）；JSON Lines 字段：`ts/level/logger/msg/request_id/trace_id`。
  纯 ASGI 请求中间件生成 `request-id`（响应头 `X-Request-ID`）并记录访问日志
  （method/path/status/duration_ms/client）。脱敏 filter：环境变量里 `*PASSWORD*/*KEY*/*TOKEN*`
  且长度 ≥8 的值，若出现在日志消息中替换为 `***`。
- **D3 · 追踪（OTel）。** `utils/tracing.py` 面向 opentelemetry-api 接口的薄封装：
  **可选依赖**——无 SDK / 未启用 = no-op（一次 INFO 说明）；启用后 exporter 可选
  console / otlp（endpoint/headers 可配）。埋点：HTTP 请求 span、`rag.query`、
  `incident.analyze`（含检索子 span）。`trace_id` 注入日志 formatter（contextvar）。
  测试用假模块注入覆盖"有 SDK"路径（沿用 `tests/_optional_deps.py` 模式）+
  env_new 实测真 SDK（pip 安装于运行环境，**不进 pyproject**）。
  **Langfuse**：文档给出其 OTLP trace 摄入 endpoint 直连配方，不新增依赖（"可选"）。
- **D4 · 认证与会话。** 密码：PBKDF2-HMAC-SHA256（20 万次迭代，`pbkdf2_sha256$iter$salt$hash`
  格式，`secrets.compare_digest` 恒时比较）；配置支持 `password`（明文，便捷）或
  `password_hash`（推荐），`scripts/hash_password.py` 生成哈希。会话：
  `secrets.token_urlsafe(32)`，服务端**只存 sha256(token)**（新表 `sessions`），
  cookie `oa_session` HttpOnly/SameSite=Lax/TTL 可配（默认 12h，惰性过期清理）。
  失败审计：新表 `auth_events`（login_ok/login_fail/logout + client + request_id）；
  按客户端 IP 限速（默认 10 次/分钟 → 429）。
- **D5 · RBAC 与回环旁路。** 角色 `admin` / `viewer`；显式策略表——默认
  **GET=viewer、写操作=admin**，例外白名单：`POST /api/v1/rag/query`、
  `POST /api/v1/incidents/analyze`、`POST /api/log/analyze|ocr`、巡检运行类 POST 归 admin。
  回环旁路 `auth.bypass_loopback: true`（默认）——桌面壳/本机浏览器无缝（桌面兼容的关键）；
  远端客户端必须登录。进程内测试 harness 主机 `testclient` 视同回环（ASGI 无法被远端伪造），
  远端模拟用 `TestClient(client=("10.x.x.x", 1234))`（starlette 1.6 已支持 `client=` 参数，
  已实测）。环境变量覆盖：`OA_AUTH_ENABLED`、`OA_AUTH_BYPASS_LOOPBACK`（容器/验证用）。
- **D6 · 安全门禁（"未完成鉴权前不得公开部署"）。** 启动时评估：
  **非回环绑定 +（认证关 or 任一用户密码为空/admin123）→ 拒绝启动**并打印修复指引；
  回环 + 空密码 → 警告放行。同步：`.env.example` 明文默认密码改占位符；
  `config.yaml` 去掉 `${OA_AUTH_PASSWORD:admin123}` 默认值；CI docker 冒烟补
  `--env OA_AUTH_PASSWORD=<非默认>`（门禁生效后原命令会拒启动）。
- **D7 · 契约与回退。** legacy `/api/*` 行为不变（回环场景）；`/api/v1/metrics`
  既有行保留（只增不减）；前端改动先打 tag `ui-before-security`（回退 =
  `git checkout ui-before-security -- ui/templates/ ui/static/`）。新模块全部纯标准库，
  冻结版兼容（不改 spec；第 6 周重建绿色版时自动带上）。
- **D8 · 中间件顺序**（纯 ASGI，避开 BaseHTTPMiddleware 对 SSE 的缓冲问题）：
  请求上下文(request-id) → 指标 → 认证门 → 路由。SSE/流式端点仅透传不读 body。

**本周不做**：不引入 prometheus_client / otel-sdk 为 pyproject 硬依赖；
不动前端既有页面结构与视觉；不改 legacy API 契约；不碰 master 与其他工作树；
不做真实 PR 合并与发布（第 6 周）；推送远端需用户二次授权。

## 三、实施步骤（TDD：先测后改 → ruff/pyright 自审 → 提交；每步全量回归）

- [x] **w5-0** 工作树+环境+基线：`uv sync --frozen --dev` + 857 passed 复现 + 本计划提交。
- [x] **w5-1** 结构化日志：`utils/request_context.py`（contextvar）、`utils/logger.py`
      升级（JsonFormatter + 脱敏 filter + format 开关）、`ui/middleware.py`
      RequestContextMiddleware（request-id + 访问日志）；测试：JSON 可解析、
      request_id 贯通、响应头存在、脱敏生效、text 默认零变化。
- [x] **w5-2** 指标扩展：`utils/metrics.py` 增 gauge/histogram + TYPE/HELP 渲染
      （旧 API 与旧行不动）、路由基数归一器、MetricsMiddleware；测试：直方图桶/累计、
      归一表、`/api/v1/metrics` 旧断言仍绿 + 新行断言。
- [x] **w5-3** OTel 追踪：`utils/tracing.py`（no-op 降级 + console/otlp exporter +
      span 上下文管理器）、埋点 HTTP/RAG/诊断、trace_id 进日志；测试：假 otel 模块注入、
      no-op 路径、span 名/属性断言。
- [x] **w5-4** 安全基础：`utils/security.py`（哈希/恒时比较/门禁评估/用户解析，
      兼容旧 `username/password` 配置合成 users）、`sessions`+`auth_events` 表与存取
      （`utils/database.py`）、`scripts/hash_password.py`、`config.yaml`+`.env.example`
      清理（注意 config.yaml 含 `${}` → 用 Python 脚本改，走 LF）；测试：哈希往返、
      门禁矩阵（非回环×认证关/默认密码/空密码/合规）、表存取。
- [x] **w5-5** 认证端点：`ui/routers/auth.py`——`POST /api/auth/login`（cookie 下发、
      429 限速、审计）、`POST /api/auth/logout`、`GET /api/auth/session`
      （loopback/session 两种模式自述）；测试：登录成功/失败/限速/登出/会话查询。
- [x] **w5-6** RBAC 中间件：`ui/middleware.py` AuthGateMiddleware（豁免表 + 策略表 +
      回环旁路 + 401/403/302 分支）、`create_app` 接线（含请求上下文/指标中间件）；
      测试：回环直通、远端 401→登录→放行、viewer 越权 403、豁免路径、SSE 透传；
      **全量回归（既有 857 必须全绿）**。
- [x] **w5-7** 前端：tag `ui-before-security` → `login.html`（登录页，深浅主题一致）+
      `index.html` 顶栏会话控件（会话模式下显示用户/登出）+ 401 跳转处理；
      验证链：node --check → playwright 截图（浅/深）→ DOM 断言（登录→回跳→登出）→ GLM-4V 目检。
- [x] **w5-8** E2E 运行验证（真实服务，无模型依赖路径）：① 严格模式
      （`OA_AUTH_BYPASS_LOOPBACK=0` + 非默认密码，:7863）实测未登录 401→登录→访问→登出；
      ② 默认模式（:7864）回环无缝 + `desktop_app.py --backend` 启动兼容 + `/api/health` 公开；
      ③ `/api/v1/metrics` 抓取含新直方图；④ `OA_LOG_FORMAT=json` 下日志行 JSON 抽检；
      ⑤ env_new + 真 OTel SDK（console exporter）实测 span 输出；
      ⑥ 门禁实测：`--host 0.0.0.0` + 默认密码 → 拒绝启动并打印指引。
      验证脚本落 `E:/YunweiAgent/.hermes/tmp/`，结果写报告。
- [ ] **w5-9** 文档与报告：`docs/reports/week5-security-observability-report.md`（验收对照 +
      实测数据 + 口径声明）；README 认证章节重写（关闭 P0-5 / P2-13 叙述）；
      `docs/维护日志.txt`、`docs/交接文档.txt` 同步；执行记录勾选；优化清单状态更新。
- [ ] **w5-10** 全量回归（pytest/ruff/pyright/hermes verify --skip-start）+ 收尾
      （端口/进程清理、待办盘点）+ 推送授权请示 + 微信通知（频控失败不重试）。

## 四、验证口径

- 全部单测离线（`--disable-socket`），新测试不引入网络；
- 运行验证用真实服务，覆盖 401/403/放行/登出/指标/日志/门禁 6 条路径；
- OTel 真 SDK 验证用 env_new 临时安装（不进 pyproject），结果如实报告；
- 覆盖率与 ci 门槛沿用：全量 passed + 覆盖率不低于当前 80%；
- 所有数字取自实际运行输出，不手抄；报告声明环境与合成案例口径。

## 五、风险与回退

| 风险 | 缓解 |
|---|---|
| 中间件影响既有 857 测试 | 回环/testclient 直通 + 默认配置本地零变化；w5-6 后全量回归把关 |
| SSE / 流式端点被中间件破坏 | 纯 ASGI 透传；w5-6 专门测流式首字节 |
| 门禁误伤 docker/CI | 同步改 CI workflow 注入非默认密码；本地先跑门禁矩阵测试 |
| config.yaml `${}` 编辑损坏 | 用 Python 脚本改（已知 patch 工具校验坑）；改后 config 加载测试 |
| 前端视觉回归 | tag `ui-before-security`；视觉验证链四步走 |
| 锁文件/依赖污染 | 本周零 pyproject/uv.lock 变更（D1/D3） |
