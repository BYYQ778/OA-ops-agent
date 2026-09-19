# 第 2 周实施计划 — RAG 2.0（分支 feat/hybrid-rag）

状态：实施中（2026-09-17 开工）。本文件随后续步骤更新勾选。

## 目标

把基础向量检索升级为可解释、可配置的现代检索链，为第 3 周评测与第 4 周根因诊断提供检索底座：

```
查询规范化 → BM25 + Dense 并行检索 → RRF 融合 → Reranker（可选）
→ 去重与文档限流 → 阈值判定 → 带精确引用回答 / 证据不足明确拒答
```

## 现状基线（开工侦察结论）

| 项 | 现状 | 目标 |
|---|---|---|
| 检索 | Chroma `similarity_search` 纯稠密 | BM25+Dense+RRF，可配 Reranker |
| 切块 | `split_text` 固定 500 字/50 重叠 | 标题/段落/页语义切块（可切回 fixed） |
| 引用 | 来源 + chunk_index | 文档 + 页码/章节 + Chunk ID（uid） |
| 拒答 | 仅靠 Prompt 要求 | 相似度/分数阈值 + 明确拒答文案（配置化） |
| Agent 路由 | `NEXT_ACTION` 字符串 + 正则解析 | Pydantic 结构化输出（含 JSON 修复回退） |
| 并发 | `/api/kb/*` async def 内跑同步重活（导入期间整站阻塞，P1） | 同步端点线程池 + 变更互斥锁；SSE 逐 token 走线程池 |

关键兼容性事实：**现有 Chroma 数据无需迁移**——BM25 索引直接从现有集合构建；
嵌入模型本次不更换（执行记录明确「换 Embedding 时新建版本化索引并评测后切换」，
留给第 3 周评测数据决策）。

## 新模块（全部纯 Python、可离线测试、CI core 环境不下载模型）

| 模块 | 职责 | 要点 |
|---|---|---|
| `utils/bm25.py` | 分词 + BM25 检索 | 自实现 BM25（k1=1.5, b=0.75，IDF 正下限修正）；jieba 分词，缺失时降级 regex bigram |
| `utils/retrieval.py` | HybridRetriever / 查询规范化 / RRF / 去重限流 / 阈值判定 | 依赖注入式（dense 与语料由调用方提供），不依赖 Chroma，可直接单测 |
| `utils/chunking.py` | 语义切块 | 标题/段落分组 + 超长段落句界回退 + 页级（PDF）元数据；旧 `split_text` 不动 |
| `utils/rerank.py` | BGE Reranker 可选件 | 懒加载 CrossEncoder；不可用/未下载时直通降级（日志一次） |
| `utils/structured.py` | Pydantic 结构化输出 | `with_structured_output` 首选 → JSON 提取修复回退 → 校验失败安全默认 |

## 配置面（config.yaml 新增，默认即 lite）

```yaml
knowledge_base:
  retrieval:
    mode: lite                 # lite（默认，随绿色版分发）| quality
    hybrid_enabled: true
    dense_top_k: 10
    bm25_top_k: 10
    rrf_k: 60
    final_top_k: 5
    max_per_document: 2
    rerank: false              # quality 预设 true
    rerank_model: BAAI/bge-reranker-v2-m3
    thresholds:
      min_dense_similarity: 0.35
      min_bm25_score: 0.30
      refuse_message: "抱歉，知识库中未找到相关信息，请补充相关文档后重试。"
  chunking:
    strategy: semantic         # semantic | fixed
```

（阈值初值由实现阶段校准，第 3 周评测集调参后固化。）

## 兼容性承诺

- 旧 API 契约、前端（含 SSE）、`/api/kb/*` 路由与字段全部不变；
- 旧 594 用例保持全绿，新增用例同样满足「全离线、不联网、不下载模型」；
- 检索不可用/组件缺失时逐级降级：hybrid → dense → 旧 `_retrieve_context` 行为。

## 实施步骤（TDD：先测试后实现；每步自审后提交）

- [x] 0. 环境基线：本 worktree（feat/hybrid-rag @ f87ac88）+ uv 环境 + 594 基线复验 ✅
- [x] 1. `utils/bm25.py` + `utils/retrieval.py`：分词、BM25、RRF、去重限流、阈值 ✅（48 用例）
- [x] 2. `utils/chunking.py`：语义切块 + 页级元数据；`chunk_uid` 稳定 ID ✅（23 用例；
      PDF 页级解析 `parse_pdf_pages`；标题紧跟正文的首行识别已在运行验证中修复）
- [x] 3. 接入 `knowledge_agent`：HybridRetriever 替换 `_retrieve_context`（保留降级）、
      引用格式 v2、拒答路径、导入写新元数据 ✅（14+ 用例，含路由/接线）
- [x] 4. `utils/structured.py` + 路由改造：Pydantic 决策替代 NEXT_ACTION 正则 ✅（16 用例）
- [x] 5. `utils/rerank.py`：可选重排序组件（fake 与缺失路径测试）✅（8 用例）
- [x] 6. P1 修复：KB 端点线程池化 + 变更互斥锁；SSE 逐 token 线程池拉取 ✅（5 用例）
- [ ] 7. 全量回归：pytest + ruff + pyright + compileall；env_new 运行验证
      （导入 → 带页引用问答 → 拒答 → 删除 → 索引/KG 同步；前端冒烟）
- [ ] 8. 文档同步（同步清单核对）+ 提交汇总；推送另行授权

## 验收标准

1. 检索链可配置生效：混合检索、RRF、去重限流、阈值拒答均有测试覆盖；
2. 引用含 文档/页或章节/Chunk ID（PDF 页级、非 PDF 章节级）；
3. 语义切块为新导入默认（fixed 可回退）；旧数据无需重导入即可享受混合检索；
4. 路由走 Pydantic 校验（含两条回退路径的测试）；
5. Reranker 未下载时零报错降级；quality 组件不进入核心安装体积；
6. 旧 594 用例全绿 + 新增用例全绿；ruff/pyright/compileall 绿；
7. 运行时回归：导入耗时不劣化、问答引用正确、拒答可复现、删除同步正常。

## 风险与回退

- **回退**：全部改动在新分支/worktree；出问题用 `git checkout f87ac88` 或切回
  quality-baseline worktree。前端零改动，数据零迁移。
- **jieba 依赖**：约 7MB 纯 Python；已确认用户批准方案（缺失时自动降级，
  不阻塞导入流程）。
- **阈值初值**：未经过评测集校准前，保守取值（宁可多拒答不误答）；
  第 3 周用评测集校准后固化。
- **推送通路**：github.com:443 直连被重置，已改用部署密钥 + SSH over 443
  （remote `ssh-origin`）；本分支推送同样走该通路。

## 与后续周衔接

- 第 3 周评测：本阶段产出结构化检索结果对象（含分数、来源、引用），评测直接消费；
  拒答率与引用覆盖率为评测指标，本阶段保证可测量。
- 第 4 周诊断：IncidentEvent 检索复用 HybridRetriever（历史故障检索同理）。
