# 第 4 周实施计划 — 智能根因诊断 Agent（分支 feat/incident-rca）

状态：开工（2026-09-18）。本文件随后续步骤更新勾选。

## 目标（六周计划 · 第 4 周）

把「巡检 + 日志 + 告警」三类输入统一成事件与证据，产出**带证据链的根因诊断报告**，
形成同时体现 AI 与运维能力的旗舰功能：

- 流程：巡检/日志/告警输入 → 标准化事件 → 按时间与服务关联 → 检索知识库与历史故障
  → 生成候选根因 → 证据验证与排序 → 输出诊断报告与处置建议。
- 统一数据模型：`IncidentEvent`（时间/来源/服务/主机/指标/严重级别/原始证据）、
  `Evidence`（类型/内容/来源/时间/关联强度）、
  `DiagnosisReport`（根因/置信度/证据链/备选原因/处置建议/引用资料）。
- 纪律（硬性）：**只读诊断**，绝不自动执行修复命令；每条结论必须携带证据，
  **禁止仅凭模型常识输出根因**；证据不足时明确「不确定」，不强凑两条证据。
- 新增接口：`POST /api/v1/incidents/analyze`、`GET /api/v1/incidents/{incident_id}`、
  `POST /api/v1/rag/query`、`GET /api/v1/evals/latest`、`GET /metrics`；
  保留 `/api/kb/*` 兼容层（前端/桌面版零中断）。

验收标准（六周计划原文）：

- 准备不少于 30 个标准故障案例；
- Top-1 根因判断准确率达到 80%；
- 每份报告包含根因、置信度、至少两条证据和可执行建议；
- 支持从日志分析或巡检页面一键发起根因分析。

## 现状基线（开工侦察，2026-09-18）

- 工作树 #4：`E:\YunweiAgent\oa-ops-agent-incident-rca`（分支 `feat/incident-rca`
  @ `9b3c5c5`，基于第 3 周尖端）；uv venv 就绪后跑全量冒烟。
- 后端：`ui/server.py` 957 行 / 53 条 legacy 路由（巡检/日志/SSL/网络/数据库/安全/
  监控/配置/知识库/图谱/命令）；`ui/routers/` 目前仅 `system.py`。
  无 `/api/v1/*`、无 `/metrics`、无 incident 相关代码（全新建）。
- 现成输入源（无需新造数据通道）：
  - 日志：`utils/log_analysis.py`（10 类 FAULT_RULES 纯正则）+ `/api/log/analyze`；
  - 巡检：`agents/inspection_agent.py`（模拟）/ `inspection_real.py`（真实）
    + `utils/scheduler.py` + `/api/inspect/*`；
  - 告警：`utils/alert.py`（邮件/钉钉/企业微信）+ 数据库 alert 记录；
  - 专项诊断：`agents/{db_inspector,network_diag,security_audit,ssl_monitor}.py`。
- 可复用检索与评测：`utils/retrieval.py` HybridRetriever（分层证据门）
  + 第 3 周 `evals/`（24 篇语料、results/latest.json、指标/报告工具链）。
- 持久化：`utils/database.py` SQLite 单例（巡检/告警/日志/会话表，`_init_tables` 可扩展）。
- 已知待修（第 1 周记录 e/f，原定随本阶段）：db_inspector 3 处 + inspection_real 3 处（§八）。

## 一、数据模型（utils/incident_models.py）

Pydantic v2 模型（纯 stdlib + pydantic，CI 可测）：

- `IncidentEvent`：id、timestamp、source（log|inspection|alert|manual）、service、host、
  metric、severity（info|warning|error|critical）、raw（原始证据文本/结构化片段）、tags。
- `Evidence`：id、kind（log_pattern|inspection_metric|kb_citation|history_match）、
  content、source、timestamp、strength（0-1）、refs（关联事件/文档）。
- `CandidateCause`：cause_id、title、category、score、evidence_ids[]。
- `DiagnosisReport`：incident_id、created_at、status（ok|uncertain）、
  root_cause: CandidateCause | None、alternatives[]、evidence[]（全链）、
  suggestions[]（含引用）、citations[]、uncertain_reasons[]、
  meta（llm_used / degraded / read_only=True）。
- **模型层即携带纪律校验**：status=ok ⇒ root_cause 非空且证据 ≥2 条；
  status=uncertain ⇒ uncertain_reasons 必填。

## 二、信号与规则（agents/incident_rules.py）

- 日志信号：复用/扩展现有 FAULT_RULES 错误模式 → 信号（候选 cause + 证据抽取）；
  **顺带补 Oracle / SQL Server 错误码**（第 1 周 P3 遗留）。
- 巡检信号：磁盘使用率、内存、CPU 负载、服务/进程存活、端口、证书到期等阈值 → 信号。
- 告警信号：alert 记录（warning/error）→ 信号。
- 每条规则给出：cause_id、标题、类别、基础置信度、匹配证据（原文子串锚点）。
- 纯函数、确定性、全离线单测；LLM 不参与候选生成。

## 三、管线（agents/incident_agent.py）

`analyze(inputs, options)` 只读管线（依赖注入，CI 用 fakes）：

1. 标准化：三类输入 → `IncidentEvent[]`；
2. 关联：按时间窗口 + 服务/主机聚合（无显式服务时按事件内容推断，推断依据记录在证据里）；
3. 检索：HybridRetriever 查知识库（引用进证据链）；历史故障 = incidents 库既往报告
   （冷启动为空则跳过并如实标注，不做假数据）；
4. 候选：规则信号 → 候选根因；
5. 验证与排序：逐候选核验（证据 ≥1 规则命中 + 引用背书 / 双信号互证），按证据强度排序；
6. 报告：Top-1 + 备选；证据不足 → status=uncertain（明示原因，不硬凑）；
   建议从 KB 处置段落抽取（带引用），无 KB 时给规则内置的通用建议并标注来源。

降级纪律：KB/嵌入不可用 → 规则信号仍产出报告（meta.degraded 标注）；
LLM 可选（`include_llm`，默认关）：只允许在已有证据/候选范围内组织语言，**不可新增根因**；
不可用/失败时确定性报告照常输出。

## 四、持久化（utils/database.py 扩展）

- 新增 `incidents` 表（id、created_at、service、host、status、root_cause、
  report_json、events_json）与 `save_incident / get_incident / list_incidents`；
  与现有表同一 SQLite（oa_ops.db）；单测沿用隔离 OA_DATA_DIR 风格。

## 五、API（ui/routers/incidents.py，挂 /api/v1）

| 接口 | 说明 |
|---|---|
| POST /api/v1/incidents/analyze | body：log_text / inspection_results / use_latest_inspection / service / host / include_llm；返回 DiagnosisReport（含 incident_id） |
| GET /api/v1/incidents/{incident_id} | 取既往报告（404 语义明确） |
| GET /api/v1/incidents | 列表（辅助，供前端历史/演示） |
| POST /api/v1/rag/query | 结构化检索（question → hits + citations + refused；复用检索链，不跑 LLM） |
| GET /api/v1/evals/latest | 读 evals/results/latest.json（缺失时 `available:false`，不报错） |
| GET /metrics | 最小 Prometheus 文本计数（incidents_total / uncertain_total / rag_queries_total / refusals_total…）；第 5 周扩展 OTel/完整指标 |

- 挂载：`create_app` 增加 `include_router(create_incidents_router(...))`；
  legacy 路由一组不动（兼容层）。
- 同步 analyze 端点用普通 `def`（FastAPI 线程池，与第 2 周 P1 修复同姿势），
  避免阻塞事件循环。
- API 测试全离线（fakes 注入检索/存储）。

## 六、案例集与评测（evals/）

- `evals/cases/incidents_v1.jsonl`：**≥30 条**标准案例（目标 34 左右），覆盖：
  Tomcat 5xx / OOM / 磁盘满 / CPU 高负载 / MySQL / Redis / Oracle / SQLServer /
  网络·DNS·SSL / 服务停止 / 安全事件 / **证据不足（应判 uncertain）**。
  - 字段：id、category、input{log_text | inspection[] | alert}、service/host、
    expected_cause_id、expected_keywords[]（证据锚点，须为输入原文子串）、
    expected_status(ok|uncertain)、notes。
  - 校验：`scripts/validate_incident_cases.py`（schema/计数/锚点/配额）。
- `scripts/run_incident_eval.py`：离线实跑（env_new；复用第 3 周 24 篇语料建独立 KB 索引）
  → Top-1 准确率、uncertain 判定正确率、证据完整性（≥2 证据比例）、建议覆盖率；
  输出 results JSON 与报告章节（docs/reports/）。
- 目标：Top-1 ≥80%；如实报告（不足则给原因与改进路径，不修饰）。

## 七、前端一键入口（ui/templates/index.html + ui/static/）

- 日志分析页：分析结果下方「发起根因分析」按钮 → 调 analyze（带当前日志文本）
  → 报告面板（根因/置信度/证据链/建议/引用/不确定标记）。
- 巡检页：结果区「一键根因分析」（带最近一次巡检结果）→ 同面板（复用组件）。
- 遵守前端工作流：dev=1 热刷新、node --check、playwright 截图 + GLM-4V 目检、
  DOM 坐标实测；**动手前 git tag（ui-before-rca）保证可回退**。

## 八、遗留缺陷修复（第 1 周记录 e/f，随本阶段）

- `db_inspector`：listening_ports 地址解析正则误分类 / Oracle NOARCHIVELOG 误报 /
  audit_cron_jobs 死分支 —— 先补测试复现，再修。
- `inspection_real`：check_nginx PID 列错位（parts[-1]）/ check_oa_service 内存列假设脆弱 /
  auto 模式 SSH+local 全失败不降级 —— 先补测试复现，再修（降级行为走最小改动）。

## 九、实施步骤（TDD；每步自审后提交）

- [ ] 0. 环境基线：worktree#4 + uv venv + 全量冒烟（759 passed 预期）
- [ ] 1. incident_models.py + 单测（证据纪律在模型层）
- [ ] 2. incident_rules.py（日志/巡检/告警信号；补 Oracle/SQLServer 规则）+ 单测
- [ ] 3. incident_agent.py 管线（标准化/关联/候选/排序/报告）+ 单测（fakes）
- [ ] 4. database.py incidents 表 + 存取方法 + 单测
- [ ] 5. 知识检索接入（HybridRetriever 复用 + 引用进证据链）+ 历史故障检索 + 单测
- [ ] 6. 案例集 ≥30 + 校验器 + run_incident_eval 实跑 → Top-1 目标 ≥80%
- [ ] 7. /api/v1 路由（incidents/rag/evals/metrics）+ API 单测 + 兼容回归
- [ ] 8. 前端一键入口 + 视觉验证（tag 可回退）
- [ ] 9. e/f 缺陷修复 + 测试兜底
- [ ] 10. 全量验证（pytest/ruff/pyright/compileall + 运行验证）→ 文档同步
      → 推送按惯例另行请示

## 验收对照

| 六周计划验收标准 | 本阶段测量 |
|---|---|
| ≥30 个标准故障案例 | evals/cases/incidents_v1.jsonl 计数（校验器输出） |
| Top-1 根因准确率 ≥80% | run_incident_eval 结果 JSON（如实口径） |
| 报告含根因+置信度+≥2 证据+建议 | 模型校验 + 案例 runner 断言 |
| 日志/巡检页一键发起 | 前端按钮 + 运行验证（接口实测/截图） |

## 风险与回退

- 只读、无自动执行：管线不产生任何命令执行路径（测试断言覆盖）。
- LLM 默认关；Ollama/DeepSeek 不可用不影响确定性报告（降级标注）。
- 全部改动在 worktree #4；回退 = revert 分支提交 / 忽略本工作树；
  主目录、绿色版、桌面版不触碰；前端改动有 git tag 快照。
- 知识库证据依赖嵌入模型（本机已缓存）；CI 用例全用 fakes，不下载模型、不联网。

## 待用户确认 / 授权

1. 案例集为**合成场景**（公开安全、可复现），沿用第 3 周语料做知识库；
   如希望使用真实故障案例请提供素材。
2. 完成后推送 GitHub（按惯例届时单独请示）。
3. 可选顺带项（不纳入本周核心，需要请明示）：jieba 转正为正式依赖、
   路由器重复检索优化。

## 与后续周衔接

- 第 5 周可观测性：`/metrics` 从最小实现扩展为 OpenTelemetry/Prometheus 全量；
  incident 管线补埋点。
- 第 6 周 README/演示：根因诊断 Top-1 数据与演示素材——以本阶段结果 JSON 为唯一数据源。
