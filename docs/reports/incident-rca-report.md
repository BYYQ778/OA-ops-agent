# 第 4 周 · 智能根因诊断 — 评测与验收报告

生成时间: 2026-09-18 ｜ 分支: feat/incident-rca ｜ 结果数据: `evals/results/incidents-v1.json`

## 一、验收口径与结论

| 六周计划验收标准 | 实测 | 结论 |
|---|---|---|
| 标准故障案例 ≥30 个 | 35 条（dev 25 / holdout 10） | ✅ |
| Top-1 根因判断准确率 ≥80% | **30/30 = 100%**（dev 25/25、holdout 5/5） | ✅ |
| 每份报告含根因、置信度、≥2 条证据、可执行建议 | 报告完整性违规 **0** 条（模型层强制校验） | ✅ |
| 支持从日志分析/巡检页面一键发起根因分析 | UI 按钮 + 真实运行验证通过（DOM 断言 + 截图目检） | ✅ |
| 证据不足时标明不确定、不强凑 | 5 条「应判不确定」案例全部正确标 uncertain | ✅ |

## 二、评测设置

- 案例集：`evals/cases/incidents_v1.jsonl`，35 条合成场景 —— 30 条可诊断（覆盖
  资源/应用/HTTP 5xx/MySQL/Redis/Oracle/SQLServer/网络/安全/OS）+ 5 条设计为
  「应判不确定」（单条弱信号、无信号、低于阈值、弱信号混叠）。
- 知识库：复用第 3 周语料（24 篇、222 块）；检索引擎与问答链同源（HybridRetriever +
  分层证据门），诊断侧使用放开门 `0.45 / 5.0`（引用召回优先；问答链冻结阈值
  0.89 / 10.25 不受影响，两者同一引擎、不同场景配置）。
- 全流程离线、零 LLM：候选根因全部来自规则信号 + 知识库引用背书，确定性可复现。
- 观测：知识库引用命中 **31/35** 案例（平均 2.09 条/案例）；平均 198ms/条；
  平均信号 2.0 条/案例。

## 三、复现命令

```bash
# 校验案例集（结构/锚点/信号 sanity，退出码 0 = 通过）
env -u PYTHONPATH python scripts/validate_incident_cases.py

# 实跑评测（需 RAG 依赖环境，复用第 3 周语料建独立索引；CI core 不跑本脚本）
HF_HUB_OFFLINE=1 env -u PYTHONPATH <python> scripts/run_incident_eval.py
#  → evals/results/incidents-v1.json（逐案例明细 + 环境 meta + 拆分统计）
```

## 四、诚实声明（口径边界）

- 案例集为**合成数据**（手工编写的典型故障场景），非生产事故数据；100% 是
  「标准案例集」上的结果，**不代表开放环境准确率**。
- 案例与规则出自同一编写者，存在系统性风险（案例措辞与规则模式高度可及）。
  缓解：保留 dev/holdout 划分并分拆如实呈现；今后任何规则/阈值调整必须
  复跑 holdout 防过拟合。
- 改进路径：收集真实巡检日志/工单作为第二批案例（可标注即纳入）；条件允许时
  增加「LLM 参与候选生成」的对照实验。

## 五、关联工件

- 案例与运行器：`evals/cases/incidents_v1.jsonl`、`evals/incident_cases.py`、
  `evals/incident_runner.py`、`scripts/run_incident_eval.py`
- 管线与模型：`agents/incident_agent.py`、`agents/incident_rules.py`、
  `utils/incident_models.py`、`agents/incident_kb.py`
- API：`POST /api/v1/incidents/analyze`、`GET /api/v1/incidents/{id}`、
  `POST /api/v1/rag/query`、`GET /api/v1/evals/latest`、`GET /metrics`
- UI：日志分析页 / 巡检页「一键根因分析」（tag `ui-before-rca` 可回退）
