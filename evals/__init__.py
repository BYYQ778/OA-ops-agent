"""评测体系包（第 3 周 · RAG 与 Agent 评测）。

子模块:
- dataset: 评测集加载与静态校验（纯 stdlib、离线可用）
- metrics: 检索/引用/拒答/延迟指标（纯函数）
- pipelines: 旧版行为重建与新版混合检索适配（实跑时使用）
- runner: 全量离线评测编排（产出 results JSON）
- report: Markdown 报告与图表生成
"""
