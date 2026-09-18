# OA 智能根因诊断平台 v3.0 执行记录

## 已批准的目标

在现有项目上渐进优化，兼顾 AI 应用开发与运维开发岗位；本地 Ollama 优先、云端可选，同时交付 GitHub 源码及 Windows 成品。保留 FastAPI、LangGraph、Chroma、SQLite、SSE 与现有桌面壳。

## 阶段与验收

- [ ] 1. 工程基线：可复现依赖、pytest、Ruff、Pyright、pre-commit、CI、渐进路由拆分。首批覆盖日志规则、图谱、巡检解析、SQLite、真实 API。核心覆盖率目标 80%，全项目目标 60%；必须说明统计范围，不用排除业务代码冒充达标。**（2026-09-17 本地门槛达成；远端 CI/桌面/绿色重验未完成，故不勾选）**
- [ ] 2. RAG：BM25 + Dense + RRF、可选重排序、结构化路由、证据引用、低置信度拒答。旧向量库需保留，换 Embedding 时新建版本化索引并评测后切换。**（2026-09-17 feat/hybrid-rag 主体完成：712 passed + 运行验证全链路通过；Embedding 版本化切换与阈值校准待第 3 周评测）**
- [ ] 3. 评测：至少 100 条标注问答，冻结留出集；同一数据/硬件比较旧版与新版 Recall@5、MRR、NDCG、引用、拒答、延迟。目标是验收条件，不是预先宣称的成果。
- [ ] 4. 根因诊断：统一事件和证据，至少 30 个标准案例，输出候选原因、证据和建议；证据不足时标明不确定，不强凑两条证据。只读诊断，禁止自动执行修复。**（2026-09-18 feat/incident-rca 本地完成：35 案例、Top-1 30/30、857 passed；推送与远端 CI 待做，故不勾选）**
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
  **已推送**（2026-09-17，部署密钥 + SSH over 443 通路），远端 CI 首跑全绿
  （run 35193433016：quality + docker-core；594 passed / TOTAL 75%）。
- 待办: 桌面 GUI 交互与绿色版随合并重验；PR 合并待用户授权。
- 2026-09-17 下午（第 2 周）: feat/hybrid-rag（worktree 2）完成 RAG 2.0 主体 ——
  混合检索/语义切块/引用 v2/结构化路由/重排序可选件/P1 并发修复；712 passed +
  ruff（严格范围）/pyright 全绿 + env_new 运行验证全链路通过（导入期间
  health 最大延迟 19ms；命中引用与契约拒答均实测）。第 2 周分支已推送，
  远端 CI 全绿（run 35211380542：quality 712 passed / 78% + docker-core）。
- 2026-09-17 晚（第 3 周）: feat/rag-evaluation（worktree 3）评测体系主体完成 ——
  evals/：24 篇合成语料 + 128 条评测集（dev88/holdout40）+ 校验器/指标/管线/运行器/
  报告生成，759 用例全绿；实跑（同一语料/嵌入/硬件，全量 128 条）：Recall@5
  74.1%→94.4%（+20.4pp）、MRR 0.609→0.807、引用覆盖率 74.1%→98.1%（holdout 97.0%）；
  阈值校准（dev 网格）→ 分层证据门冻结 0.89/10.25 ∥ 0.60/7.75；无证据拒答率
  0.0%→75.0%（dev 84.6%、holdout 57.1%；近域负例与正例分数重叠，85% 目标未达、
  如实记录并给出改进路径）；报告+图表自动生成（docs/reports/rag-eval-report.md）。
  修复：评测脚本重建索引清空目录（防重复写入污染）。utils/retrieval.py 新增分层门控
  （默认行为兼容）。
- LLM 端到端子集已实跑（云端 DeepSeek，用户授权）：系统级无证据拒答率 100%（门控 3 +
  回答级 5）、有依据回答率 77.3%、平均 3.9 步、端到端 mean 3.9s/P95 6.4s；发现路由
  器重复检索（平均步数偏高）与 2 例回答级误拒（改进项，见报告）。
- 推送与 CI 核验已完成（2026-09-17 推送 feat/rag-evaluation；远端 CI 全绿 run 35223665459：759 passed / TOTAL 78%，docker-core 健康冒烟通过）；待办: 延迟安静环境复测（可选）；本地 Ollama 对比跑（可选）。
- 下一步: 第 4 周根因诊断（复用 HybridRetriever 与 evals 结果结构）。
- 2026-09-18（第 4 周）: feat/incident-rca（worktree 4）智能根因诊断主体完成 —— 数据模型
  （证据纪律/只读约束进模型层）、23 类信号规则（日志/巡检/告警，含 Oracle/SQLServer）、
  只读诊断管线（多源互证/证据验证排序；<2 条证据或低于 0.6 分 → 明确不确定）、
  incidents 表持久化、HybridRetriever 适配器（引用进证据链）、/api/v1 五接口
  （incidents/rag/evals/metrics，/api/kb/* 兼容不动）、日志/巡检页一键入口
  （tag ui-before-rca 可回退）、第 1 周 e/f 六处缺陷修复（ss 端口解析/NOARCHIVELOG
  误报/cron 死分支/nginx PID 列/内存列/auto 模式诚实失败）。
- 实跑: 35 条标准案例（dev25/holdout10）Top-1 30/30（100%）、不确定判定 5/5、报告
  违规 0；知识库引用覆盖 31/35；平均 198ms/条。口径与边界见
  docs/reports/incident-rca-report.md（合成案例集，非生产数据）。
- 验证: 857 passed（全离线）+ ruff 严格范围全绿 + pyright 全范围 0 错误 + 运行验证
  （服务 :7862 起，日志页/巡检页一键入口 DOM 断言 + 截图 + GLM-4V 目检全过）。
- 待办: 推送 + 远端 CI（另行按授权执行）；可选: jieba 转正、路由器重复检索优化、
  Ollama 对比跑。
- 下一步: 第 5 周可观测性与安全（/metrics 扩展 OTel/Prometheus、结构化日志、鉴权）。
