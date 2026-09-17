# RAG 与 Agent 评测体系（evals/）

第 3 周（分支 feat/rag-evaluation）为 OA 智能根因诊断平台建立的**离线可复现评测**：
把第 2 周的混合检索链（BM25 + Dense + RRF + 阈值拒答）与旧版纯稠密检索放在
**同一语料、同一嵌入模型、同一硬件**上对比，产出可写进 README / 简历的量化结果。

## 目录结构

- `corpus/`：评测语料 —— 24 篇合成的 OA/运维 Markdown 文档（公开安全、随仓库分发）
- `dataset/qa_v1.jsonl`：评测问答集（JSONL，一行一条）
- `results/`：评测结果 JSON（脚本产出，随仓库提交作为证据）
- 模块：`dataset.py`（加载/校验）、`metrics.py`（指标）、`pipelines.py`（旧版重建 + 新版适配）、
  `runner.py`（编排）、`report.py`（报告与图表）

## 语料规范（corpus/）

- 每篇约 1500–2400 字中文 Markdown；结构：`# 标题` + `## 章节`
  （概述 / 故障现象 / 排查步骤 / 常见原因 / 处置与恢复 / 预防措施 / 附录）。
- 内容为合成的通用 OA 运维知识（虚构主机名 10.x 私网 IP、标准端口），
  不含任何真实企业与个人数据；文档读起来应像真实内部手册（不出现"合成/示例/评测"字样）。
- 覆盖：OA 服务、Tomcat/HTTP 502·503、OOM、磁盘满、CPU 高负载、MySQL、Redis、Oracle、
  SQL Server、网络·DNS·SSL、安全基线、运维流程（备份/巡检/变更/应急/值班）。
- 写入真实感细节：错误码、命令、日志路径、阈值数字；单节正文 ≤600 字；
  将部分关键结论分散在「预防措施」「附录」等节（检验检索覆盖）。

## 评测集规范（dataset/qa_v1.jsonl）

| 字段 | 说明 |
|---|---|
| id | `q###`，唯一 |
| question | 10–45 字自然提问；含错误码/现象词；避免 yes-no 问法 |
| category | 见 `dataset.py` 的 CATEGORIES（9 类） |
| answerable | true/false |
| gold_docs | 可回答：包含答案的语料**文件名**（精确，含 .md）；无证据：空数组 |
| gold_snippets | 可回答：语料原文**精确子串**锚点（≥6 字，空白折叠后匹配）；无证据：空数组 |
| split | dev / holdout（分层 70/30；**holdout 冻结**，不参与调参） |
| route_expected | 可选（LLM 子集：期望工具路由；平时留空） |
| notes | 一句话标注说明（考查点） |

撰写纪律：

1. 锚点必须能在标注文档中直接命中（校验脚本自动验证，先写文档再复制锚点）；
2. 无证据条目必须确保语料确实没有支撑（人工复核 + 关键词检查）；
3. 至少 1/3 问题为改述型（不含文档标题关键词），用于检验语义检索；
4. 同一问题不得重复改写出现；宁可覆盖不同知识点。

## 校验

```bash
env -u PYTHONPATH python scripts/validate_eval_set.py              # 全量（含配额）
env -u PYTHONPATH python scripts/validate_eval_set.py --no-strict  # 仅结构
```

## 指标口径与复现

- 文档级 Recall@5 / MRR / NDCG@5（Top-5 命中折叠为唯一文档序列后比对）；
- 引用准确率（Top-5 命中文本命中锚点的比例）、引用覆盖率；
- 无证据拒答率；检索延迟 mean/P95；LLM 子集（30 条）的回答级指标与延迟。
- 一键复现：`scripts/run_eval.py`（离线，产出 `results/*.json`）；
  阈值校准：`scripts/calibrate_thresholds.py`。详细口径见 `docs/reports/` 中的评测报告。
