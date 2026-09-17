# 第 3 周实施计划 — RAG 与 Agent 评测体系（分支 feat/rag-evaluation）

状态：开工（2026-09-17 晚）。本文件随后续步骤更新勾选。

## 目标（六周计划 · 第 3 周）

把第 2 周的混合检索链放到可复现的量化评测下，产出招聘方认可的对比结果：

- 建立不少于 100 条运维问答评测集，覆盖：OA 服务故障 / 502·503·OOM·磁盘满 /
  MySQL·Redis·Oracle·SQL Server / 网络·DNS·SSL / 安全基线与运维手册 / 无法从知识库回答的问题。
- 指标：Recall@5、MRR、NDCG；引用准确率与引用覆盖率；有依据回答率、无依据拒答率；
  工具选择准确率；平均响应时间与 P95 延迟；Agent 平均推理步骤与失败率。
- 验收：新检索链 Recall@5 较旧版提升 ≥10pp（若旧版 >85% 则保持 ≥85% 且提升 MRR）；
  引用覆盖率 ≥90%；无证据拒答率 ≥85%；自动生成新旧对比报告与图表。
- **同一数据 / 同一硬件 / 同一嵌入模型**比较新旧两版；目标是验收条件，不是预先宣称的成果。

## 现状基线（开工侦察结论，2026-09-17）

| 项 | 旧版（master 行为，基线重建） | 新版（feat/hybrid-rag 基线） |
|---|---|---|
| 切块 | split_text 固定 500 字 / 50 重叠 | 语义切块（标题/段落/页级，可回退 fixed） |
| 检索 | Chroma 纯稠密 similarity_search top-5 | BM25 + Dense + RRF（k=60），final_top_k=5，max_per_document=2 |
| 拒答 | 无（始终返回） | 证据阈值门（初始 0.35 / 0.30，待校准） |
| 引用 | 来源 + chunk_index | 文档 + 页码/章节 + chunk_uid（format_citations） |
| 重排序 | 无 | 可选 CrossEncoder（默认关；模型未下载，本轮不评估） |

关键事实：

- 嵌入模型 paraphrase-multilingual-MiniLM-L12-v2 已缓存本机 HF cache（458MB）
  → 评测全离线、可复现；两版使用同一模型。
- BM25 分词依赖 jieba（可选、缺失降级 bigram）：评测环境安装 jieba，
  并做「jieba vs 降级分词」消融说明（是否升格为正式依赖留待评审）。
- 工作树 #3：`E:\YunweiAgent\oa-ops-agent-rag-evaluation`（分支 feat/rag-evaluation @ cf1b3c7）；
  uv venv 就绪，71 用例冒烟通过。
- 硬件/版本元数据写入结果 JSON（CPU、内存、Python、依赖版本），供报告追溯。

## 一、评测语料（evals/corpus/）

24 篇合成的 OA/运维文档（公开安全、随仓库分发、版本冻结），覆盖计划全部主题类别；
每篇约 1500–2400 字，含真实感错误码、IP/端口、操作步骤与章节标题（同时检验语义切块与引用渲染）。
**评估专用、独立索引，不导入用户真实知识库。**

清单（初定，写作时允许微调）：

- OA/应用：OA系统应用架构与故障分级说明 / OA门户Tomcat服务故障排查手册 /
  Tomcat 502与503错误处置指南 / JVM内存溢出(OOM)应急处置手册 /
  服务器磁盘空间不足处置指南 / 服务器CPU与高负载排查手册
- 数据库：MySQL数据库故障排查手册 / MySQL慢查询与连接数问题处置指南 /
  Redis缓存故障应急处置手册 / Redis内存与持久化排查指南 /
  Oracle数据库日常故障处置手册 / Oracle表空间与归档日志问题处理指南 /
  SQLServer数据库故障排查手册 / SQLServer备份与还原操作指南
- 网络：网络连通性故障排查手册 / DNS解析故障处置指南 /
  SSL证书过期与配置问题处置指南 / 服务器端口与服务访问检查手册
- 安全/运维流程：服务器安全基线配置规范 / 生产环境变更管理流程规范 /
  数据备份与恢复操作手册 / 系统巡检操作手册 / 生产故障应急响应流程 / 运维值班与告警处理规范

## 二、评测集（evals/dataset/qa_v1.jsonl）

目标 120+ 条（硬性 ≥100）≈ 100 条可回答 + 20 条无证据（含近域难负例）。

配额（初定）：oa_service 12 / http_5xx 12 / resource 12 / mysql 12 / redis 11 /
oracle 11 / sqlserver 10 / network 14 / security_ops 12 / 无证据 20。

字段：id、question、category、answerable、gold_docs[]、gold_snippets[]（原文精确子串锚点，≥4 字）、
split、route_expected（LLM 子集用）、notes。

标注纪律：

- 可回答条目：gold_snippets 必须是对应文档的原文子串（校验脚本自动验证）；每条至少 1 个锚点。
- 无证据条目：gold_docs 为空；语料确实无支撑（写作时逐条复核 + 检索低分抽查）。
- 划分：分层 70/30 → dev（调参）/ holdout（**冻结**，定稿后不再参与任何调参；报告注明）。
- 校验：scripts/validate_eval_set.py 一键校验 schema / 计数 / 重复 / 锚点存在性 / 配额，输出统计表。

## 三、指标口径（必须写入报告）

全部基于「同一语料 + 同一嵌入 + 同一硬件」：

- 文档级 Recall@5 / MRR / NDCG@5：Top-5 命中按名次折叠为唯一文档序列，与 gold_docs 比对
  （旧版块级洪泛会自然体现在低召回上）。
- 引用准确率：Top-5 命中文本包含 gold_snippets 锚点的比例（span 级）。
- 引用覆盖率：可回答问题中，回答上下文含 ≥1 条指向 gold 文档引用的比例（format_citations 输出）。
- 无证据拒答率：无证据条目中证据门触发拒绝（refused=True）的比例。
- 检索延迟：mean / P95（毫秒，纯检索不含 LLM）。
- LLM 端到端子集（30 条：22 可答 + 8 无证据）：有依据回答率（回答含《gold 文档名》引用或正确拒答）、
  答案级无依据拒答率、工具选择准确率（router 首跳决策 vs route_expected）、平均推理步数、
  失败率、端到端 mean/P95 延迟。
  - 默认 Ollama qwen3:8b（本地优先、免费；预留 1–2 小时）；如用户授权，可另用 DeepSeek
    云端跑同一子集做加速对照（费用几元内）。

## 四、Harness 设计（离线可单测；重依赖只在实跑时加载）

| 模块 | 职责 | CI 可测 |
|---|---|---|
| evals/dataset.py | 数据集加载 / 校验 / 划分统计（纯 stdlib） | ✅ fakes |
| evals/metrics.py | Recall@k / MRR / NDCG@k / 引用 span / 拒答 / 延迟统计（纯函数） | ✅ |
| evals/pipelines.py | LegacyPipeline（重建旧版行为）+ HybridPipeline（复用 utils/retrieval.py） | ✅ 注入 fakes |
| evals/runner.py | 编排：建双索引（临时目录）→ 全量离线评测 → results JSON（含元数据） | 实跑 env_new |
| evals/report.py | Markdown 报告 + matplotlib 图表（PNG，英文标注避免字体坑） | — |
| scripts/run_eval.py | CLI 入口 | — |
| scripts/calibrate_thresholds.py | dev 网格搜索 → 冻结 → holdout 终评 | — |
| scripts/validate_eval_set.py | 数据集静态校验 | — |

- 旧版重建：split_text(500/50) + langchain_chroma similarity_search top-5（无阈值无引用）；
  与 master 代码路径逐点核对（距离度量等），写入报告。
- 新版：语义切块 + HybridRetriever（配置来自 config.yaml，rerank 默认关）。
- 新单测只用 fakes（不联网 / 不下载模型），与现有 712 用例共同保持全绿。

## 五、阈值校准

- 网格：min_dense_similarity 0.10–0.60（步长 0.02）× min_bm25_score 0.05–0.80（步长 0.05）；
  在 dev 上求「拒答率（无证据）≥85% 前提下最大化可回答通过率」，报告联合曲线。
- 冻结值写入 config.yaml 与报告；holdout 终评一次。
- 附：final_top_k / max_per_document / rrf_k 敏感性小表（仅供报告，不改默认）。

## 六、实施步骤（TDD；每步自审后提交）

- [x] 0. 环境基线：worktree#3 + uv venv + 71 用例冒烟 ✅（2026-09-17）
- [x] 1. evals/ 骨架：dataset.py + validate 脚本 + 离线单测
- [x] 2. 语料 24 篇 + 评测集 128 条（108 可答 + 20 无证据）撰写 + 校验通过
      （dev 88 / holdout 40；锚点 171；子代理撰写 + 校验器/关键词排查复核）
- [x] 3. metrics.py + 单测（14 用例）
- [x] 4. pipelines.py（旧版重建 + 新版适配）+ 小规模烟测（8 用例）
- [x] 5. runner 全量离线评测实跑 ✅（全量 128 条：Recall@5 94.4%、拒答率 75.0%）
- [x] 6. 阈值校准（dev 网格）→ 冻结 → holdout 终评 ✅（分层门控 0.89/10.25 ∥ 0.60/7.75）
- [x] 7. LLM 子集真实运行 ✅（云端 DeepSeek，用户授权）：系统级无证据拒答率 100%
      （门控 3 + 回答级 5）、有依据回答率 77.3%、平均 3.9 步、端到端 mean 3.9s/P95 6.4s
- [x] 8. 报告 + 图表（docs/reports/ 含 LLM 子集区块）+ 惯例文档同步 ✅
- [ ] 9. 全量本地验证（pytest/ruff/pyright/compileall + 运行验证）→ 推送（授权后）→ CI 核验

## 验收对照

| 六周计划验收标准 | 本阶段测量 |
|---|---|
| Recall@5 提升 ≥10pp（或旧版>85% 时保持 ≥85% 且 MRR 提升） | holdout 文档级新旧对比 |
| 引用覆盖率 ≥90% | holdout 引用覆盖率 |
| 无证据拒答率 ≥85% | holdout 拒答率 |
| 自动对比报告 + 图表 | scripts/run_eval.py 一键产出 |

## 风险与回退

- 本阶段不动应用行为（除 config 阈值终值，可 revert）；回退 = revert 分支提交；旧数据零迁移。
- 指标不达标时如实报告 + 原因分析（不修饰、不造数）；若因语料难度差距不足 10pp，
  按「≥85% 保持 + MRR 提升」口径评估并明示。
- reranker / BGE-M3（quality 模式）不在本轮：模型未下载即不评估，报告明确标注为未覆盖项。
- jieba：评测环境装上并做消融；不改变正式依赖声明（留评审）。

## 待用户确认 / 授权

1. 合成语料 + 合成评测集方案（公开安全、可复现）；如坚持使用真实内部文档，请明示。
2. LLM 子集默认本地 Ollama；是否授权 DeepSeek 云端加速对照（费用几元内）。
3. 完成后推送 GitHub（按惯例届时单独请示）。

## 与后续周衔接

- 第 4 周根因诊断复用 HybridRetriever 与 /api/v1/evals/latest 读取本轮 results JSON 结构。
- 报告结论将作为第 6 周 README / 简历量化的唯一数据源（无实测不写入）。
