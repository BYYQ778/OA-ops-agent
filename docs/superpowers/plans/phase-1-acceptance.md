# 第 1 阶段验收记录

状态：实施中，不代表 v3.0 或第 1 阶段全部验收通过。

## 保护已有版本

2026-09-16 在仓库外保存完整 Git bundle、未提交配置补丁、源码/data/dist 副本。9009 个文件逐一 SHA-256 比对，0 差异。主目录仍保持原分支和原配置；新增代码仅存在于 `chore/quality-baseline` 工作树。

## 部署改动

容器使用 Python 3.12 和固定 uv 0.12.3，从 uv.lock 安装。`core` 构建阶段不含大型 RAG/OCR 依赖，供自动化 API 冒烟使用；默认 `runtime` 包含 rag/ocr extras，不含 Windows 桌面壳。构建不下载模型权重，首次实际使用 RAG/OCR 前需准备模型。

Compose 需要 2.24.0 或更高版本（可选 env_file）。默认仅映射 `127.0.0.1:7860`；认证完成前不对公网开放。Docker 内 `localhost:11434` 指向容器自身，连接宿主 Ollama 时需在专用配置中设置 `host.docker.internal:11434`（Docker Desktop），不能直接沿用源码环境的 localhost。只读挂载 config.yaml 时 UI 保存配置不可用；这不是运行时配置管理功能。

原 Compose 依赖未安装的 curl 检查首页，现统一使用 Python 检查 `/api/health`。健康检查仅证明 HTTP 服务可达，不证明大模型或向量库已就绪，需另看 `kb_state`。

## 环境限制

本机未发现 docker 或 gh 命令。Docker 构建与 Linux 容器启动由 CI 执行，本地未验证；在远端 workflow 实际成功之前，不宣称容器验收完成。原 env_new 的 Python 3.11 已失效，因此仍需全依赖环境和桌面运行验收。

## 完成条件

- 本地离线 pytest、Ruff、Pyright 指定范围、编译检查通过，并记录命令和版本。
- 同时记录核心模块和全库覆盖率，不能只报告挑选模块的高值。
- 未完成的全库 60%、核心 80%、远端 CI、桌面回归，必须保持未完成状态。
- 提交前检查敏感文件、用户配置和备份没有进入 Git。

## 2026-09-16 本地验证记录

本批次建立基础设施并修复测试发现的问题，**第 1 周尚未完整验收**。

- Python 3.12.13 / uv 0.12.3；真实解析生成锁文件（235 包），`uv sync --locked --dev` 与 `uv lock --check --offline` 成功。未安装 rag/ocr/desktop extras，未下载模型权重。
- `python -m pytest --cov --cov-report=term --cov-report=json --cov-report=xml -q`：126 passed，2 条第三方弃用警告（Starlette/AnyIO、PyPDF2）；测试未连接外部服务。最终复核使用独立 COVERAGE_FILE，避免并发覆盖率文件锁。
- 全量统计 `agents/ui/utils`，分支覆盖开启：总覆盖率 29%；SQLite 64%、图谱存储 54%、日志分析 61%、SSH/本机巡检模块 51%、文档解析 79%、调度器 96%。核心范围和整体均不满足本周门槛，不作为简历的达标结果。
- `ruff check .`（全库语法级规则）、`ruff check --select E,F,I,W ui/routers tests`、`pyright`（新 Router 和测试范围）通过，Pyright 0 errors / 0 warnings。
- `python -W error::SyntaxWarning -m compileall -q agents ui utils main.py desktop_app.py`、`git diff --check` 通过。
- CI 配置包含 core Docker 构建和容器 `/api/health` 冒烟、覆盖率上传；未推送，远端 CI **未执行**。pre-commit 配置验证通过，读取 YAML 后按真实 hook 参数运行两组 Ruff 均通过；hook 完整安装执行仍待验证。

## 本批次行为改进与风险

- 网络工具在命令/DNS/socket 前验证主机和端口，拒绝选项注入、非法域名标签与无效端口，保留内网下划线主机名与尾点域名。
- SSH 采集缺失/异常不再误报健康；非法内存值不会导致未初始化变量错误；磁盘 >100% 的真实 df 数据仍保留容量告警。测试同时检查报告到持久化状态解析的结果。
- 文档切块拒绝非法参数，且自然边界与大 overlap 组合保证游标前进；仍使用原固定切块算法，**不是第 2 周语义切块**。
- 引入 `create_app`，首步提取健康/开发版本路由；领域 Router 进一步拆分待继续。健康接口与旧 API 契约保留，关闭后台服务用于 core/offline 验证，不等于验证真实模型。
- 在原主目录重新确认只有已有 `config.yaml` 修改，SHA-256 与备份记录一致。优化分支单独将 Ollama 设为默认，未复制私有 `.env`。

## 下一批次

补充数据库批事务/删除、图谱文档删除同步、知识库导入与降级、完整巡检和主要 API 行为测试，逐步达成覆盖率门槛；验证全依赖源码环境与桌面功能。审查完成后可保留本地提交；GitHub 推送、PR、合并及 Release 均需另行授权。

回滚：当前改动仅在隔离分支，不影响原目录运行；切回原目录即可使用原版本。不得将备份或私有配置加入仓库。跨目录恢复方式详见仓库外备份的 RESTORE.md。

## 2026-09-17 续作记录（第 1 周剩余工作）

状态：**第 1 周本地门槛达成**（覆盖率双达标 + 静态检查全绿 + 源码运行回归通过）；
远端 CI、桌面 GUI 完整交互验收、绿色版随 master 合并后重验，仍属**未验证**。

### 本次完成

- 端点测试: 新增 `test_api_operations.py` / `test_api_knowledge.py` /
  `test_system_router.py`（54 个端点主力分支，stub 工具层，完全离线）
- 缺陷修复: `/api/commands` 大括号截取（原实现对当前 commands.js 恒报
  `Extra data`）；实测 6 分类 138 命令可解析，已加回归用例
- 模块测试: db_inspector / security_audit / inspection_agent /
  inspection_real / network_diag / ssl_monitor / kg_store / database /
  dashboard / config（新建或扩展现有文件；`tests/_optional_deps.py` 使
  CI core 环境可在缺 RAG 依赖时导入 knowledge_agent）
- 修复裸 pytest / CI 导入路径: pyproject 增加 `pythonpath = ["."]`
  —— 裸 `pytest` 与 CI 的 `uv run pytest` 不把项目根放入 sys.path，
  `import ui` 会 ModuleNotFoundError（CI 首次运行的必挂隐患，已修复并复验）
- 静态检查（本地，命令与 CI 一致）: `ruff check .`、
  `ruff check --select E,F,I,W ui/routers tests`、`pyright`（0 errors）、
  `compileall` 全部通过

### 覆盖率实测（branch=true，全库 agents/ui/utils）

- 语句: 29.1% → **75.4%**（3775/4994）；分支: 23.4% → **74.6%**（1272/1704）
- 核心: 巡检 100% / 98.8%、日志 100%、图谱 96.8%、SQLite 100%、
  主要 API 85.5%、doc_parser 78.7%；其余模块 db_inspector 95.9%、
  security_audit 97.0%、network_diag 97.5%、ssl_monitor 93.2%、
  dashboard 94.8%、config 100%、scheduler 97.2%
- → 「核心 ≥80%、整体 ≥60%」**本地达标**（pytest 594 passed）
- 未达标（如实记录）: knowledge_agent 8.7%；entity_extractor / kg_builder /
  alert / ai_reporter / doc_parser_v2 / mineru_adapter 0%（LLM 重依赖，
  排期 v3 第 2-3 周随 RAG 2.0 补测）

### 运行回归（env_new 3.11.9 全依赖，worktree 源码）

- 源码模式（7862）: health ready ≈10s；Ollama 自动拉起；inspect/run（本机
  真实采集 5 项 + 入库 + 仪表盘推送）、log/analyze（502 规则）、
  kb list/stats、kg/stats、commands 全部 200
- KB 全链路: 导入（3 块 → Chroma → KG 5 实体/10 边，≈11 分钟，LLM 抽取慢，
  一次 JSON 失败自动正则兜底）→ 问答（≈1.5-3 分钟，带引用回答）✓
- 桌面壳: `desktop_app.py --backend`（7860）health / inspect/status /
  log/analyze 通过；GUI 壳未改动

### 新增发现（不修复，排入后续周）

- P1 /api/kb/* 长任务阻塞事件循环（导入期间整站无响应；master 同样）
- P2 本机 Windows 磁盘/内存检测依赖 wmic（新版系统已移除）→「无数据」
- P3 日志规则未覆盖 Oracle/SQL Server 错误码
- P3 db_inspector 审计准确性 3 处缺陷（listening_ports 正则误分类 /
  NOARCHIVELOG 误报 / crontab 死分支）+ inspection_real 解析脆弱点
  （nginx PID 列错位等）—— 维护日志「发现」e/f，建议随第 4 周诊断能力一并修
- 信息: starlette 1.6.0 TestClient 整体缓冲流式响应（无限 SSE 不可走 HTTP 测试）

### 远端 CI 验证（2026-09-17 完成，原「仍未验证」项之一转已验证）

- **run 35193433016 全绿**（chore/quality-baseline 推送后首跑）：
  quality job 全步骤通过（Ruff ×2 / Pyright / 594 passed 24.41s /
  覆盖率上传 / compileall）；docker-core job 通过（core 镜像构建 +
  容器 /api/health 冒烟）。
- 远端覆盖率与本地一致：TOTAL 4994 语句 75%（分支 1704，63 partial）。
- 推送通路备注：github.com:443 直连被重置，经部署密钥 + SSH over 443
  （remote `ssh-origin`）推送；详见 交接文档 2026-09-17 条目。

### 仍未验证（保持未完成状态）

- 桌面 GUI 完整交互与绿色版 —— 待合并 master 后按第 6 周流程重验
