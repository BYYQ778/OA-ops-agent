# 第 6 周计划：产品化与求职展示（release/v3.0.0）

## 目标

把五周的技术成果转换成招聘方与用户能快速理解的证据：发布 `v3.0.0` Release 与 Windows 绿色版；
README / 架构 / 变更 / API / 安全文档齐备；演示 GIF 与 3~5 分钟演示视频；输出简历描述、项目介绍与面试问答。

## 背景（起点状态）

- 第 1~5 周 PR #1~#5 已全部合并 master（合并提交 `1ae1ec4` / `3bf2b29` / `071a7d0` / `c4fd2e7` / `685cfef`），
  远端 master CI 全绿；本分支 `release/v3.0.0` 从 `685cfef` 建立。
- 基线复现：**927 passed**（本工作树，2026-09-19）。
- 关键量化成果（全部有实测证据，报告在 `docs/reports/`）：
  - 工程：594 → 927 用例；覆盖率 29% → 81%；Ruff/Pyright 全绿；CI 双作业（quality + docker-core 冒烟）
  - RAG：Recall@5 **74.1% → 94.4%**（+20.4pp）、MRR 0.609 → 0.807、引用覆盖率 74.1% → 98.1%、
    无证据拒答 0% → 75.0%（dev 84.6%）；30 题 LLM 端到端子集系统级拒答 100%
  - 根因诊断：35 案例 **Top-1 30/30**、不确定判定 5/5、报告违规 0、平均 198ms/条
  - 可观测与安全：严格模式 E2E 15/15；启动安全门禁实测；OTel 真 SDK span + trace_id 实测

## 交付物清单

1. **v3.0.0 Release**：GitHub tag + Release notes；绿色版 zip 附件（先探测上传通路，不通则如实说明并给出备选）
2. **README 重写**：前 30 秒展示——解决什么问题 → 动态演示 GIF → 架构图 → RAG 新旧评测结果 →
   根因诊断示例 → 一键启动方式
3. **文档**：`ARCHITECTURE.md`、`CHANGELOG.md`、`docs/API.md`、`docs/SECURITY.md`、评测报告（已有，纳入索引）
4. **演示素材**：README 动态 GIF、3~5 分钟演示视频（5 场景：导入运维文档 → 带引用 RAG 回答 →
   故障日志与巡检数据 → 根因/证据链/处置建议 → Trace/指标/自动化测试）、GitHub 社交预览图（1280×640）
5. **求职材料**：简历描述、1 分钟 / 3 分钟项目介绍、常见面试问答（交付到用户 Obsidian，私人）
6. **绿色版重建**：v3.0.0 代码重打包 + 三形态回归（源码 / 桌面壳 / 绿色版）

## 执行步骤

- [x] B0 工作树与基线：本文件 + 927 passed 复现（hermes verify 记录）
- [ ] B1 README 重写（数字全部实测值）
- [ ] B2 ARCHITECTURE.md（mermaid 架构图：三形态 / 双进程 / RAG 链 / RCA 管线 / 观测安全）
- [ ] B3 CHANGELOG.md（Keep a Changelog；v2.5.0 → v3.0.0）
- [ ] B4 docs/API.md（从 app.openapi() 核对）+ docs/SECURITY.md
- [ ] B5 演示素材（演示数据准备 → GIF → 演示视频 → 社交预览图）
- [ ] B6 求职材料 → Obsidian
- [ ] C1 推送 release 分支 → PR → CI → 合并 master
- [ ] C2 绿色版重建（备份 data/config → PyInstaller → 恢复 → 实测）
- [ ] C3 三形态回归 + 发布前检查清单
- [ ] C4 tag v3.0.0 + Release（含附件上传探测）
- [ ] C5 仓库门面（description / topics / Discussions / issue 模板 / 社交预览）
- [ ] D 收尾（第 6 周总结 → Obsidian；微信通知；技能/记忆沉淀）

## 验收标准

- 发布前检查全过：测试、CI、依赖安全、敏感信息、Docker（CI 覆盖）、绿色版、README、评测报告、演示流程
- README 前 30 秒六要素齐备；所有数字均为实测值（无证据不宣称）
- 绿色版按真实用户视角实测通过（启动 → 就绪 → 巡检/KB/RCA 冒烟）
- Release 发布且有可下载产物（或如实说明通路限制与备选）
- 面试材料经得起追问：每个数字都能指回报告 / CI 记录

## 边界与风险

- 演示视频为自动化录制的界面流程（无真人配音）；如需口播版另行补录
- 绿色版 zip 约 1~1.5GB；GitHub Release 附件上传需先探测 `uploads.github.com` 通路
- 内部过程文档（维护日志 / 交接文档等）按现状保留于仓库（治理项另议）
