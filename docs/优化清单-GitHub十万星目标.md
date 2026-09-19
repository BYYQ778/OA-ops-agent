# OA-ops-agent 全面优化清单 —— 目标 GitHub 10k+ star

> 审查日期：2026-08-14 ｜ 审查基线：本地 master @ b6eebdb（含 13 个未推送提交）
> 现状：3 stars / 0 forks / 0 releases / 0 discussions / 无 description、topics、CI、测试

## 审查结论（一句话）

产品本身已具备 10k star 的**功能底子**（本地优先 + 绿色版 exe + 双 LLM 轨 + 多 Agent 架构 + 安全加固），
但**分发层（GitHub 门面）、工程可信度（测试/CI/文档）、增长引擎（内容/社区）三项几乎为零**——
当前仓库对访客的"转化率"约等于裸仓库，star 不可能自己涨。

---

## P0 · 立即止血（本周可完成，0~1 天）

| # | 事项 | 现状证据 | 动作 |
|---|---|---|---|
| P0-1 | 推送 13 个本地提交 | pushed_at 停在 08-04，含安全修复/桌面版/绿色版重大更新 | `git push origin master` |
| P0-2 | GitHub 仓库元数据 | description=None, homepage=None, topics=[] | 填 description（中英）、topics（rag, llm, self-hosted, ops, fastapi, langchain, ollama, ai-agent）、Social Preview 图 |
| P0-3 | 开启 Discussions + issue/PR 模板 | has_discussions=False | Settings 开启；`.github/ISSUE_TEMPLATE/bug_report.yml`、`feature_request.yml`、`PULL_REQUEST_TEMPLATE.md` |
| P0-4 | 发布第一个 Release | releases=[] | v2.5.1 tag + Release，附绿色版 zip（1.7GB<2GB 上限，可传）与源码包 |
| P0-5 | 修复认证配置"撒谎" | config.yaml `auth.enabled: true` 但 server.py 无任何认证中间件；.env.example 明文 admin123 | 二选一：实现登录认证（推荐，P2-17）或先改 `enabled: false` 并在 README 说明；.env.example 默认密码改为占位符 |
| P0-6 | 公开仓库里的内部文档治理 | docs/维护日志.txt、交接文档.txt、会话记忆_*.md 已公开 | 内部记录移出仓库（.hermes/ 或私有），沉淀为公开 CHANGELOG.md + ARCHITECTURE.md（P2-20） |

## P1 · 转化率核心（2~4 周，决定 star 增速的上限）

| # | 事项 | 现状证据 | 动作 | 预期效果 |
|---|---|---|---|---|
| P1-1 | README 全面重写 | 纯中文 10KB、零截图、端点数字过时(47 vs 实测54)、无对比表/roadmap | 英文为主版（全球流量）+ 中文版（README_zh.md）；顶部 30 秒 GIF 演示；仪表盘/巡检/知识图谱/问答四张截图；架构图；"为什么存在"叙事；对比表；Roadmap；徽章（CI/coverage/downloads） | ★ 最大单项杠杆 |
| P1-2 | 演示资产 | 仓库零图片/视频 | 录 GIF（绿色版双击启动→仪表盘→巡检→知识问答流式输出）；B站+YouTube 演示视频；可选在线 demo（HF Spaces/Streamlit demo 模式） | ★★ |
| P1-3 | CI 上线 | 无 .github/workflows | GitHub Actions：ruff lint + pytest + 前端 JS 语法检查 + 打包冒烟；README 点亮徽章 | ★★ 信任度 |
| P1-4 | 测试骨架 | 无 tests/，22 项 E2E 清单为手工且不在仓库 | pytest + 首批 20~30 用例：巡检正则、日志规则库 10 种故障、KG 构建/查询、API 冒烟（TestClient）；把 22 项手工 E2E 转成脚本 | ★★ 贡献者门槛 |
| P1-5 | Release 自动化 | 手动打包 5~6 分钟 | CI 自动跑 PyInstaller + 自动上传 Release 资产（tag 触发） | ★ |
| P1-6 | CHANGELOG.md | 缺 | 从 git log 倒推 v2.0→v2.5.1，之后 conventional commits + 自动生成 | ★ |

## P2 · 工程债（1~3 月，决定"产品级"还是"大脚本"）

| # | 事项 | 现状证据 | 动作 |
|---|---|---|---|
| P2-7 | 拆单体 | knowledge_agent.py 1143 行 / db_inspector.py 1015 行 / server.py 899 行（54 端点一个文件）/ index.html 2025 行 | server.py 按域拆 `ui/routers/`（inspect/log/kb/kg/sec/net/db/dashboard）；knowledge_agent 拆 retriever/graph/chat 三层；前端按模块拆（保持无框架或仅 esbuild 打包） |
| P2-8 | 工程化元数据 | 无 pyproject.toml/setup.py | pyproject.toml：版本元数据、ruff/mypy/pytest 配置、可选 `pip install` 入口 |
| P2-9 | 依赖锁定 | requirements 全 `>=` —— tokenizers 0.23.1 事故的根源 | uv.lock 或 pinned requirements.txt + 已知坑写入文档 |
| P2-10 | 类型注解渐进补 | knowledge_agent.py 39 个 def、server.py 68 个 def，带返回值注解 = 0 | ruff 规则渐开，新代码强制，核心文件优先 |
| P2-11 | i18n | 前端/提示词/报告全硬编码中文 | UI 语言切换（至少英文）；提示词模板外置到独立文件 |
| P2-12 | Docker 修复 | LABEL version=2.2 过时；清华源硬编码（国际用户直接失败）；compose `version:` 字段已废弃 | 双轨源（默认 PyPI，注释内给国内镜像）；进 CI 验证；healthcheck 对齐 /api/health |
| P2-13 | 登录认证落地 | 见 P0-5 | 实现 session 认证中间件 + 前端登录页 + 默认强密码策略；文档同步 |
| P2-14 | 插件/规则扩展机制 | 巡检项、日志规则为硬编码 | 自定义巡检规则 DSL/JSON 配置化 —— 社区贡献的天然入口 |
| P2-15 | ARCHITECTURE.md（英文） | 缺 | 系统架构图 + 模块职责 + 数据流 + 扩展指南 |
| P2-16 | 绿色版分发优化 | 1.7GB 单文件体验差 | 7z 压缩分卷（预估可压到 1GB 内）；提供"精简版"（去 OCR 模型） |

## P3 · 增长引擎（持续，12~24 个月）

| # | 事项 | 动作 |
|---|---|---|
| P3-17 | 定位叙事 | 统一为 "Local-first AI Ops Assistant / 运维新手的 AI 导师"——数据不出本机、零 API Key、绿色版 exe 双击即用；对比表对标 Uptime Kuma（监控）/ Netdata（指标）/ Dify（LLM 应用平台），强调"会动手的 AI 运维员"差异 |
| P3-18 | 内容分发 | 每版本发布：HN Show HN、Reddit r/selfhosted + r/LocalLLaMA、V2EX、掘金/知乎/CSDN/公众号；首篇主打"我把 OA 巡检交给 AI 后…"实战文 |
| P3-19 | 社区 | README 放微信群二维码；Discord 服务器；Discussions 置顶 Roadmap 征集 |
| P3-20 | 贡献生态 | good-first-issue 标签池（每版本 3~5 个）、CONTRIBUTING.md（中英）、CODE_OF_CONDUCT.md |
| P3-21 | 版本节奏 | 每月 1 minor + CHANGELOG；semver + conventional commits；tag 自动发 Release |
| P3-22 | 里程碑运营 | 100/500/1k star 感谢帖 + 路线图更新；监控 stars/week、clones、referral 来源 |
| P3-23 | 多平台 | Linux server 形态完善（当前 SSH 巡检目标为 Linux 但本体偏 Windows）→ 扩大受众 |

---

## 附：审查中发现的具体问题清单（bug/不一致）

1. **13 个提交未推送** —— 世界看到的还是 8-04 旧版
2. **config.yaml `auth.enabled: true` 但认证未实现** —— 安全诚信问题，见 P0-5
3. **README 端点数字过时**：结构树写 47 个，实测 54 个
4. **Dockerfile `LABEL version="2.2"`** 过时；清华源硬编码对国际用户不可用
5. **LICENSE 年份 2025**、作者为空泛占位
6. **requirements.txt 全 `>=` 无锁定** —— tokenizers 0.23.1 使知识库全挂的事故会重演
7. **公开 docs 含内部流水账**（交接文档/维护日志/会话记忆），不专业且可能泄露环境信息
8. **无自动化测试** —— 22 项 E2E 依赖人工且不在仓库，改动回归全靠记忆
9. **.env.example 明文默认密码 admin123**（配合 config 默认值，开箱即弱口令）
10. **绿色版 1.7GB** 直传体验差（可传但下载转化低）

## 现实预期

- 10k+ star 在运维垂直赛道是行业头部水平（参考：1Panel ~3 万、Uptime Kuma ~7 万）。
- 达成路径 = P0+P1 全部落地（门面+可信度）+ P3 内容运营持续 12~24 个月。
- **6 个月内现实里程碑：1k star**；首个增长信号是 Release 绿色版发布后 r/selfhosted 的反馈。
