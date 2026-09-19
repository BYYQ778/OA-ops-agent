# 更新日志（CHANGELOG）

本项目按「阶段分支 + PR + CI 门槛」的节奏迭代。**v3.0.0 是六周工程**（工程可信度 → RAG 2.0 → 评测体系 → 根因诊断 → 可观测与安全 → 发布收口）的收口版本，也是本仓库的**首个正式 Release**（此前迭代历史保留在 git tag）。

## [3.0.0] — 2026-09-19 · OA 智能根因诊断平台

### 新增

- **智能根因诊断（只读）**：日志 / 巡检 / 告警 → 事件标准化 → 规则信号（23 组）→ 知识库互证 → 证据验证排序 → 诊断报告（根因 · 置信度 · 证据链 · 处置建议）；证据不足明确标注「不确定」；不执行任何修复动作
- **`/api/v1` 接口组**：`incidents/analyze`、`incidents/{id}`、`incidents` 列表、`rag/query`、`evals/latest`、`metrics`（Prometheus `/metrics`）
- **RAG 2.0**：BM25 + Dense 混合检索 → RRF 融合 → 分层证据门 → 带引用回答（《文档》·第X页·§章节·chunk）；语义切块；lite / quality 双预设；无证据明确拒答
- **评测体系**：24 篇测试语料 + **128 条标注评测集**（dev/holdout 冻结）+ 指标管线 + 阈值校准 + 自动报告；**35 条根因诊断标准案例**
- **可观测与安全**：结构化 JSON 日志（request-id + 密钥脱敏）、Prometheus 指标、OpenTelemetry 追踪（可选零依赖降级）、Session 登录 + PBKDF2 + 登录限速 + RBAC、启动安全门禁（非回环 + 默认密码 → 拒绝启动）
- **工程**：`pyproject.toml + uv.lock` 锁定依赖、pytest（**927 用例，全离线**）、Ruff / Pyright / pre-commit、GitHub Actions（quality + docker-core 双作业）
- **UI**：日志 / 巡检页「一键根因分析」入口与只读诊断报告弹窗；登录页与会话控件

### 变更

- 知识库问答接入混合检索链（旧纯向量检索保留在评测对照中）
- `/api/health` 增加 `kb_state` / `kb_error`（知识库就绪状态机）
- 巡检 / 日志诊断质量修复：ss 端口解析、Oracle NOARCHIVELOG 误报、cron 死分支、nginx PID 列、内存列、auto 模式诚实失败

### 质量（均为实测数字，报告见 docs/reports/）

- pytest **927 passed**；全库覆盖率 29% → **81%**；Ruff / Pyright / compileall 全绿
- RAG：Recall@5 **74.1% → 94.4%**、引用覆盖率 98.1%、无证据拒答 0 → **75.0%**（dev 84.6%）
- 根因诊断：**Top-1 30/30**、不确定判定 5/5、报告完整性违规 0、平均 198ms/条
- CI 双作业全绿（含 Docker 镜像构建 + 容器健康冒烟）

### 阶段分支 / PR

`chore/quality-baseline` → `feat/hybrid-rag` → `feat/rag-evaluation` → `feat/incident-rca` → `feat/observability-security` → `release/v3.0.0`
（PR #1~#5 堆叠合并，合并提交 `1ae1ec4` ~ `685cfef`）

## [2.5.0] — 2026-08 ~ 09

- 对话历史持久化 + SSE 流式输出 + 知识问答 UI 重构（v2.5.0 主体）
- 桌面版 PyWebView 原生窗口；启动就绪等待（预热状态机 + health 上报 + 跳过按钮）
- 绿色版（PyInstaller）+ **业务代码外置 `app/`**（更新免重打包）+ 离线模型 / 前端资源本地化
- 安全修复：提示词注入防护、命令注入 / 路径穿越、明文 Key；本地命令参数化（`shell=False`）
- provider=ollama 自动拉起 Ollama 服务；诊断工具箱重构；使用小贴士；开发热刷新（`?dev=1`）

## [2.4.1] — 2026-08-04

- 7 项 Bug 修复 + 知识图谱力导向可视化 + LLM 热切换 + Ollama 自启 + 安全增强

## [2.4] — 2026-07-10

- 知识图谱 + Agentic RAG 多步推理 + 多格式文档解析 + Chatbot 多轮对话

## [2.3] — 2026-06

- 实时监控仪表盘 + 运维命令大全（138 条）+ 知识问答命令推荐

## [2.2] — 2026-06-10

- FastAPI + 原生前端架构（下线 Gradio）；知识库拖拽上传恢复 + Docker 文件同步

## [2.1] — 2026-06-09

- 诊断工具箱上线（SSL / 网络 / 数据库 / 安全基线）
