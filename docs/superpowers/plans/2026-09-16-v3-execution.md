# OA 智能根因诊断平台 v3.0 执行记录

## 已批准的目标

在现有项目上渐进优化，兼顾 AI 应用开发与运维开发岗位；本地 Ollama 优先、云端可选，同时交付 GitHub 源码及 Windows 成品。保留 FastAPI、LangGraph、Chroma、SQLite、SSE 与现有桌面壳。

## 阶段与验收

- [ ] 1. 工程基线：可复现依赖、pytest、Ruff、Pyright、pre-commit、CI、渐进路由拆分。首批覆盖日志规则、图谱、巡检解析、SQLite、真实 API。核心覆盖率目标 80%，全项目目标 60%；必须说明统计范围，不用排除业务代码冒充达标。**（2026-09-17：本地门槛达成 + 远端 CI 首跑全绿；桌面 GUI/绿色版重验未完成，故不勾选）**
- [ ] 2. RAG：BM25 + Dense + RRF、可选重排序、结构化路由、证据引用、低置信度拒答。旧向量库需保留，换 Embedding 时新建版本化索引并评测后切换。
- [ ] 3. 评测：至少 100 条标注问答，冻结留出集；同一数据/硬件比较旧版与新版 Recall@5、MRR、NDCG、引用、拒答、延迟。目标是验收条件，不是预先宣称的成果。
- [ ] 4. 根因诊断：统一事件和证据，至少 30 个标准案例，输出候选原因、证据和建议；证据不足时标明不确定，不强凑两条证据。只读诊断，禁止自动执行修复。
- [ ] 5. 可观测性/安全：本地结构化日志、OpenTelemetry、Prometheus、可选 Langfuse、Session/RBAC、密钥隔离。桌面启动方式兼容；未完成鉴权前不得公开部署。
- [ ] 6. 交付：经过验证的 Windows 绿色版、README/架构/安全/API/评测文档、演示素材、简历。无运行或测量证据的指标不能写成已达成。

## 执行约束

每阶段先行为测试再改动，保持旧 API 契约。阶段分支和 PR 顺序依次为 quality-baseline、hybrid-rag、rag-evaluation、incident-rca、observability-security、v3.0.0。远端 CI 与实际桌面验收未运行时必须记为待验证，不能以本地单元测试替代。推送和发布另行按用户明确授权执行。

## 2026-09-16 恢复点

- 开发分支：`chore/quality-baseline`；从 `81dd4d4` 创建。
- 优化前工作区、Git 历史、未提交配置与现有成品已备份在仓库外，并附恢复说明。
- 原 `env_new` 依赖的 Python 3.11 已失效；当前开发工作树使用 uv 管理的 Python 3.12.13。
- 原主目录的 `config.yaml` 修改保留；优化代码仅修改隔离工作树。
- 第 1 阶段实施中；尚未发布 v3.0，后续阶段尚未完成。

## 后续重点核查（静态发现，未宣称修复）

- `agents/db_inspector.py` 中 MySQL/Redis 命令仍拼接字符串并使用 shell=True；认证上线与参数化执行之前不开放网络访问。
- `agents/network_diag.py` 的 HTTPS 健康检查关闭证书验证；应区分连通性检查与 TLS 身份可信性，不能把连通视为安全。
- `utils/dashboard.py` 在线程调度器和 asyncio SSE 间直接写队列，后续需验证跨线程推送与断连清理。
- `agents/knowledge_agent.py` 升级检索时同时检查路由状态字段、异常路径、会话历史与并发行为。
- `oa_agent.spec` 和旧 bat 脚本绑定 env_new、固定模型缓存和外置代码；更换运行环境后必须单独验证完整桌面包。

## 进度记录

- 2026-09-17: 第 1 周本地门槛达成 —— pytest 594 passed（全离线）、全库覆盖率
  75.4%（分支 74.6%）、核心模块 93–100%、ruff/pyright/compileall 全绿、
  hermes verify --skip-start ok:true；env_new 源码 + 桌面 --backend 运行回归
  通过。过程中发现并修复裸 pytest/CI 导入路径隐患（pyproject pythonpath）。
- 分支 chore/quality-baseline 领先 master 7 个提交（860af8b → d0aa18c），
  **未推送**（推送需单独授权）。
- 待办: 推送 → 远端 CI 首跑；桌面 GUI 交互与绿色版随合并重验。
- 下一步: 第 2 周 feat/hybrid-rag（RAG 2.0），计划见
  docs/superpowers/plans/phase-2-hybrid-rag.md（随第 2 周分支提交）。
