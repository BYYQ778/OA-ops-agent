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
