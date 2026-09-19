# API 文档（docs/API.md）

> 本文件为端点总览与核心示例；**交互式文档（Swagger UI）** 启动服务后访问 `http://127.0.0.1:7860/docs`。
> 示例数据来自 2026-09-19 本机构建实测（空知识库环境；真实知识库部署中 `citations` 会包含引用条目）。

## 一、通用约定

| 项 | 说明 |
|---|---|
| Base URL | `http://127.0.0.1:7860` |
| 认证 | 本机回环免登录；远端客户端需先 `POST /api/auth/login`（cookie `oa_session`）。详见 [SECURITY.md](SECURITY.md) |
| 内容类型 | JSON；SSE 流式端点 `text/event-stream`；`/api/v1/metrics` 为 Prometheus 文本 |
| 错误格式 | `{"detail": "..."}` —— 401 未登录 / 403 无权限 / 404 不存在 / 429 登录限速 |
| 接口分层 | `/api/v1/*`：平台化接口（推荐集成）；`/api/*`：界面接口（兼容保留，契约稳定） |
| 只读保证 | 诊断类接口**不执行任何修复动作**；证据不足时明确回答「不确定」，不编造根因 |

## 二、核心接口（/api/v1）

### 1) POST /api/v1/incidents/analyze —— 根因诊断（只读）

请求体字段（可组合；至少提供一类信号）：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `log_text` | string | `""` | 日志文本（多行） |
| `inspection_results` | object[] | `[]` | 巡检结果列表（与巡检页记录同构） |
| `alerts` | object[] | `[]` | 告警事件列表 |
| `use_latest_inspection` | bool | `false` | 自动取最近一次巡检记录（界面「一键根因分析」用） |
| `service` / `host` | string | `""` | 关联服务 / 主机 |
| `include_llm` | bool | `false` | 可选：附加 LLM 叙述（失败自动回退确定性报告） |

**请求示例**

```json
{
  "log_text": "2026-09-18 10:02:11 [ERROR] No space left on device - /var/log/messages\n2026-09-18 10:02:30 [WARN] log rotate skipped: disk full\n2026-09-18 10:02:45 [INFO] 系统进入只读模式",
  "service": "oa-app",
  "host": "10.20.1.11"
}
```

**响应示例（真实截取，status=ok）**

```json
{
  "saved": true,
  "report": {
    "incident_id": "INC-20260919180955-777891",
    "created_at": "2026-09-19T18:09:55",
    "status": "ok",
    "root_cause": {
      "cause_id": "disk_full",
      "title": "服务器磁盘空间不足",
      "category": "resource",
      "score": 0.88,
      "evidence_ids": ["EV-1c47db7d", "EV-46bdbb7e"],
      "rationale": "规则信号 2 条（LOG-DISK）"
    },
    "alternatives": [],
    "evidence": [ "…2 条证据（规则信号；真实知识库部署时含引用证据）…" ],
    "events": [ "…2 条标准化事件（EVT-*，含时间/来源/服务/主机/严重级别）…" ],
    "suggestions": [ " …3 条处置建议（来自规则模板与知识库）… " ],
    "citations": [],
    "uncertain_reasons": [],
    "meta": {"signals": 2, "candidates": 1, "kb_assigned": 0, "history_matched": 0,
             "correlations": 0, "degraded": false, "duration_ms": 40},
    "read_only": true,
    "confidence": 0.88
  }
}
```

**证据不足时**（如仅 1 行弱信号日志）返回 `status:"uncertain"`、`root_cause:null`，仅给 `alternatives`
候选与 `uncertain_reasons` —— 宁可不判，不硬凑两条证据。

**行为要点**

- `read_only` 恒为 `true`；
- 诊断报告强制落地 SQLite，可用 `GET /api/v1/incidents/{id}` 回取；
- 首次调用会触发知识库引擎懒加载（数秒）；就绪后单次诊断毫秒级（评测均值 198ms）。

### 2) GET /api/v1/incidents · GET /api/v1/incidents/{incident_id}

```json
// GET /api/v1/incidents?limit=2  →  摘要列表
{"items": [
  {"id": "INC-20260919180747-caaa04", "created_at": "2026-09-19T18:07:47",
   "service": "oa-app", "host": "", "status": "uncertain",
   "root_cause": "", "root_title": "", "confidence": 0.0},
  {"id": "INC-20260919180729-a6ac89", "created_at": "2026-09-19T18:07:29",
   "service": "oa-app", "host": "10.20.1.11", "status": "ok",
   "root_cause": "disk_full", "root_title": "服务器磁盘空间不足", "confidence": 0.88}
]}
```

`GET /api/v1/incidents/{id}` 返回单条完整记录（含 report / events JSON）；不存在 → 404。

### 3) POST /api/v1/rag/query —— 结构化检索（不调用 LLM）

请求：`{"question": "磁盘满了怎么处理", "top_k": 3}`（`top_k` 1~20，默认 5）

```json
// 知识库为空/无证据时（真实截取）
{"available": true, "query": "磁盘满了怎么处理", "refused": true,
 "refuse_message": "抱歉，知识库中未找到相关信息，请补充相关文档后重试。",
 "hits": [], "citations": [], "context": ""}
```

知识库就绪且有证据时：`refused=false`，`hits` 为命中块（含分数与文档元数据），`citations` 为引用条目。
知识库引擎未就绪时返回 `{"available": false, "message": "...", ...}`。

### 4) GET /api/v1/evals/latest

返回最近一次评测摘要（来自 `evals/results/`）：

```json
{"available": true,
 "rag": {"meta": {"timestamp": "2026-09-17T20:13:35", "embedding_model": "…"}, 
          "legacy": "…", "hybrid": "…", "comparison": "…", "per_item": "…"},
 "incidents": "…", "files": "…"}
```

### 5) GET /api/v1/metrics

Prometheus 文本格式（`text/plain`），示例行：

```text
# TYPE oa_incidents_analyze_total counter
oa_incidents_analyze_total 3
# TYPE oa_incidents_uncertain_total counter
oa_incidents_uncertain_total 1
# TYPE oa_http_requests_total counter
oa_http_requests_total{method="GET",route="/api/health",status="200"} 12
```

覆盖：HTTP 请求计数/时长直方图（`oa_http_request_duration_seconds`）、诊断与检索计数、
`oa_build_info`、进程启动时间等（实测 58 行）。旧 `/metrics` 契约行保留兼容。

## 三、认证接口

| 端点 | 说明 |
|---|---|
| `POST /api/auth/login` | 登录（失败 401 / 限速 429）；成功下发 HttpOnly cookie `oa_session` |
| `POST /api/auth/logout` | 注销当前会话 |
| `GET /api/auth/session` | 会话自述：`{"authenticated": true, "mode": "loopback", "username": "", "role": "admin"}`（本机）；远端未登录为 `authenticated:false` |

## 四、界面接口总表（`/api/*`，共 63 个 API 端点 + 2 个页面路由）

**知识库（/api/kb，16）**：

| 端点 | 说明 | 端点 | 说明 |
|---|---|---|---|
| `POST /api/kb/import` | 导入文档（多格式/OCR） | `POST /api/kb/ask` | 单题问答 |
| `POST /api/kb/chat` | 多轮对话 | `POST /api/kb/chat/stream` | 流式对话（SSE） |
| `GET /api/kb/chat/history` | 对话历史 | `POST /api/kb/chat/clear` | 清空对话 |
| `POST /api/kb/batch-ask` | 批量问答（≤20 并行） | `GET /api/kb/list` | 文档列表 |
| `GET /api/kb/document/{name}` | 文档详情 | `POST /api/kb/delete` | 删除文档 |
| `POST /api/kb/clear` | 清空知识库 | `GET /api/kb/stats` | 统计 |
| `POST /api/kb/conversation` | 新建会话 | `GET /api/kb/conversations` | 会话列表 |
| `POST /api/kb/conversation/rename` | 会话改名 | `POST /api/kb/conversation/delete` | 删除会话 |

**巡检（/api/inspect，6）**：`POST run` 立即巡检 · `start`/`stop` 定时调度 · `GET status` · `POST adjust`（调整间隔） · `GET history`
**日志（/api/log，2）**：`POST analyze`（14 类规则，纯确定性） · `POST ocr`（截图识别）
**监控（/api/dashboard，3）**：`GET metrics` · `GET history` · `GET stream`（SSE 实时推送）
**数据库（/api/db，6）**：`POST mysql` · `POST mysql/slow` · `POST mssql` · `POST oracle` · `POST redis` · `GET overview`
**网络（/api/net，5）**：`POST ping` · `port` · `dns` · `http` · `trace`
**SSL（/api/ssl，2）**：`POST check` · `POST batch`
**安全基线（/api/sec，6）**：`POST all` · `ports` · `ssh` · `firewall` · `cron` · `login`
**知识图谱（/api/kg，4）**：`POST search` · `explore` · `path` · `GET stats`
**系统（/api，4）**：`GET /api/health`（含 `kb_state` 就绪状态机） · `GET /api/dev/version` · `POST /api/config/save` · `GET /api/commands`
**认证（/api/auth，3）**：见上表 · **v1（/api/v1，6）**：见第二节

## 五、SSE 流式端点

- `GET /api/dashboard/stream` —— 仪表盘实时推送（状态卡片/告警时间线）
- `POST /api/kb/chat/stream` —— 知识问答逐 token 流式输出

均为 `text/event-stream`，逐条发送 `data: {json}`。

## 六、错误与限速

| 状态码 | 场景 |
|---|---|
| 401 | 远端未登录（豁免表外的一切路由） |
| 403 | 已登录但角色不足（如 viewer 执行巡检调度/知识库导入等写操作） |
| 404 | 资源不存在（如诊断记录） |
| 429 | 登录限速（滑动窗口，超限拒绝一段时间） |
| 503/降级 | 知识库未就绪时相关接口返回 `available:false` 或明确错误说明，不静默失败 |
