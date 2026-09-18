# OA Ops Agent v2.5.0

基于 LangChain + RAG + Chroma 的 OA 系统智能运维助手，支持自动巡检、日志分析、知识库问答和 AI 报告生成。

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Quality](https://github.com/BYYQ778/OA-ops-agent/actions/workflows/quality.yml/badge.svg)](https://github.com/BYYQ778/OA-ops-agent/actions/workflows/quality.yml)

## 功能

- **实时监控仪表盘** — SSE 实时推送 + 5 状态卡片 + Chart.js 趋势图 + 告警时间线 + 终端日志控制台
- **巡检监控** — 自动检测端口、服务、磁盘、内存，支持 Local/SSH/Simulated 三种模式，定时调度 + 历史查询
- **日志分析** — 上传或粘贴运维日志，正则匹配 10 种常见故障（502/503/OOM/磁盘满等），输出排查建议
- **知识库问答** — 多轮对话 Chatbot + Agentic RAG 多步推理 + 知识图谱探索，SSE 流式输出 + 对话历史持久化（会话列表/切换/删除/自动标题），支持单题/批量(20题并行)
- **AI 报告** — 巡检完成后自动生成预警分析与改进策略，支持 Ollama 本地离线 / DeepSeek 云端两种后端
- **诊断工具箱** — SSL 证书过期检测、网络诊断（Ping/端口/DNS/路由/HTTP）、数据库巡检（MySQL/MSSQL/Oracle/Redis）、安全基线审计
- **OCR 识别** — 知识库支持导入截图/扫描件自动 OCR；日志分析支持上传报错截图识别后分析
- **运维命令大全** — 收录 138 条常用运维命令（6 大分类），支持按命令名/功能关键词双向检索，点击卡片展开详情

## 快速开始

**无需 API Key，开箱即用。** 默认使用本地离线模式（Ollama），所有数据不出本机。


### 桌面版（推荐，体验最佳）

双击桌面上的 **「OA运维Agent」** 快捷方式（或运行 `scripts/启动桌面版.bat`），即可像原生软件一样打开应用窗口：

- 无浏览器标签页、无控制台黑框（基于 PyWebView + Edge WebView2）
- 启动画面自动等待服务就绪后进入主界面（首次加载嵌入模型约 20~40 秒）
- 关闭窗口即退出服务，端口自动释放，无残留进程
- 再次双击自动复用已在运行的服务，不会重复启动后端
- 日志：`data/desktop.log`（桌面壳）、`data/backend.log`（后端）

> 换机器/重新生成快捷方式：运行 `scripts/生成图标.py` 生成图标，然后右键 `scripts/启动桌面版.bat` → 发送到 → 桌面快捷方式。

### 绿色版（免装 Python，可分发给同事）

运行 `scripts/打包绿色版.bat`（或 `python -m PyInstaller oa_agent.spec --noconfirm --clean`）生成 `dist\OA运维Agent\` 绿色版：

- `OA运维Agent.exe` 双击即开，**无需安装 Python / 依赖 / Ollama**
- 内置离线嵌入模型与 OCR 模型（完全离线可用）
- 业务代码外置在 exe 同目录 `app/`：以后更新代码只需跑 `scripts/更新绿色版代码.bat` 覆盖 `app/` 后重启，无需重新打包（`app/` 误删时自动回退内置副本）
- 首次运行自动生成 `config.yaml` / `.env.example`：把 `.env.example` 复制为 `.env` 填入 `OA_LLM_API_KEY` 即可启用知识库/LLM（或改 `config.yaml` 用 Ollama）
- 数据（知识库/对话/巡检/日志）在 exe 同目录 `data/`，整个文件夹可整体拷贝分发
- 桌面快捷方式已指向绿色版 exe
### 方式一：Windows 一键启动（推荐）

双击项目根目录 `scripts/启动.bat`，脚本会自动：
1. 检查 Python 环境
2. 检测/安装 Ollama
3. 拉取本地大模型（qwen3:8b）
4. 安装 Python 依赖
5. 启动 Web 服务并打开浏览器

### 方式二：命令行启动

```bash
# 1. 克隆仓库
git clone https://github.com/BYYQ778/OA-ops-agent.git
cd oa-ops-agent

# 2. 安装依赖
pip install -r requirements.txt

# 3. 安装 Ollama 并拉取模型（本地离线模式需要）
# 从 https://ollama.com 下载安装 Ollama，然后：
ollama pull qwen3:8b

# 4. 启动
python main.py
```

启动后访问 **http://127.0.0.1:7860**。

### 本地开发与验证

项目使用 `uv.lock` 固定开发和 CI 依赖。默认安装为完整 core 服务，但不会安装体积较大的 RAG、OCR 或桌面依赖：

```bash
# core + 开发工具（CI 默认）
uv sync --locked --dev

# 完整源码运行环境（RAG + OCR + 桌面）
uv sync --locked --all-extras --dev

# 质量检查
uv run ruff check .
uv run ruff check --select E,F,I,W ui/routers tests
uv run pyright
uv run pytest --cov --cov-report=term-missing
uv run python -m compileall -q agents ui utils main.py desktop_app.py
uv run pre-commit run --all-files
```

测试和轻量容器可设置 `OA_ENABLE_BACKGROUND_STARTUP=0`，防止应用启动时探测 Ollama 或预热嵌入模型。设置 `OA_DATA_DIR` 可将默认 SQLite 数据文件隔离到指定目录。测试禁止外部网络连接，不下载模型；API 测试允许事件循环所需的本机回环通信。

工程基线进展（2026-09-17 本地实测）：pytest **594 passed**（完全离线，不连接外部服务）；覆盖率如实统计 agents/ui/utils——全库 **75.4%**（目标 ≥60%）、核心模块 **93–100%**（目标 ≥80%）本地达标。全库 Ruff（语法级）+ 新 Router/测试严格范围（E/F/I/W）通过；Pyright 0 errors（当前范围：新 Router 与测试）。**远端 CI、Docker 构建与绿色版尚未实际运行验收——测试通过不等于已发布 v3.0。** 详见 [阶段验收记录](docs/superpowers/plans/phase-1-acceptance.md)。

### 方式三：Docker

```bash
docker-compose up -d
```

### 想用云端大模型？

默认使用本地 Ollama，无需任何 Key。如果你想用 DeepSeek 云端 API：

1. 去 [platform.deepseek.com](https://platform.deepseek.com) 注册，获取你自己的 API Key
2. 复制 `.env.example` 为 `.env`，填入 Key：
   ```
   OA_LLM_API_KEY=sk-你的key
   ```
3. 编辑 `config.yaml`，将 `llm.provider` 改为 `deepseek`
4. 重新启动

### 演示模式（完全离线，不需要 Ollama）

```bash
python main.py --demo
```

仅使用模拟数据 + 本地正则分析，不依赖任何外部服务。

## 配置

### 巡检模式

编辑 `config.yaml`：

```yaml
inspection:
  mode: local          # local | ssh | simulated | auto
```

| 模式 | 说明 |
|------|------|
| `local` | 本机 Windows 检测（netstat/tasklist/wmic） |
| `ssh` | 远程 Linux 服务器（paramiko） |
| `simulated` | 随机模拟数据，无需外部依赖 |
| `auto` | SSH 优先 → 本机 → 模拟，逐级降级 |

### 知识库

```yaml
knowledge_base:
  chunk_size: 500
  chunk_overlap: 50

  # RAG 2.0 混合检索（BM25 + Dense + RRF，可选重排序，证据不足明确拒答）
  retrieval:
    mode: lite                 # lite（默认，随绿色版分发）| quality（BGE-M3 + Reranker，模型单独下载）
    hybrid_enabled: true
    final_top_k: 5
    max_per_document: 2        # 每篇文档最多贡献的块数
    rerank: false              # quality 预设可开 true（需单独下载重排序模型）
    thresholds:
      # 第 3 周评测校准（dev 网格 + holdout 冻结）——分层证据门
      min_dense_similarity: 0.89    # 单通道强证据：余弦相似度 ≥0.89
      min_bm25_score: 10.25         # 单通道强证据：BM25 ≥10.25
      joint_dense_similarity: 0.60  # 双通道互证：dense ≥0.60 且 bm25 ≥7.75 也视为证据充分
      joint_bm25_score: 7.75

  # 切块策略（标题/段落/页级语义切块；fixed 为旧固定字符切块）
  chunking:
    strategy: semantic

  # MinerU 多模态解析（可选，需 pip install magic-pdf）
  mineru:
    enabled: false

  # 知识图谱
  kg:
    enabled: true
    entity_types: [TECHNOLOGY, ORGANIZATION, PERSON, LOCATION, CONCEPT]

  # Agentic RAG（LangGraph ReAct 多步推理）
  agentic_rag:
    enabled: true
    max_reasoning_steps: 5
    batch_max_questions: 20
```

### LLM 后端

```yaml
llm:
  provider: ollama           # ollama | deepseek | qwen | openai
  ollama:
    model: qwen3:8b          # 本地模型
```

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OA_LLM_API_KEY` | DeepSeek API Key | — |
| `OA_AUTH_PASSWORD` | Web 登录密码（第 5 周起生效；非回环部署必须设置非默认值，否则拒绝启动） | （空） |
| `OA_LOG_FORMAT` | 日志格式：`text` / `json`（结构化 JSON Lines） | `text` |
| `OA_TRACING` | 启用 OpenTelemetry 追踪（需 `pip install opentelemetry-sdk`） | 关闭 |
| `OA_SSH_PASSWORD` | SSH 巡检密码 | — |
| `OA_EMAIL_USER` | 告警邮箱 | — |
| `OA_EMAIL_PASSWORD` | SMTP 授权码 | — |

## 目录结构

```
oa-ops-agent/
├── main.py                    # 入口
├── config.yaml                # 配置
├── .env.example               # 环境变量模板
├── requirements.txt           # 依赖
├── Dockerfile                 # Docker 部署
├── scripts/                   # 启动 & 部署脚本
│   ├── 启动.bat               # Windows 一键启动
│   └── 打包离线部署包.bat     # 离线打包
├── agents/                    # Agent 模块（11个）
│   ├── inspection_agent.py    # 巡检（模拟 + 统一入口）
│   ├── inspection_real.py     # 真实巡检（SSH + Local）
│   ├── log_analysis_agent.py  # 日志分析（正则规则库）
│   ├── knowledge_agent.py     # 知识库 RAG + Agentic RAG + KG
│   ├── entity_extractor.py    # LLM 实体提取（KG 构建）
│   ├── kg_builder.py          # 知识图谱构建编排
│   ├── ai_reporter.py         # AI 报告生成
│   ├── ssl_monitor.py         # SSL 证书监控
│   ├── network_diag.py        # 网络诊断
│   ├── db_inspector.py        # 数据库巡检
│   └── security_audit.py      # 安全基线检查
├── utils/                     # 基础设施（11个）
│   ├── config.py              # 配置管理
│   ├── database.py            # SQLite 持久化
│   ├── dashboard.py           # 实时仪表盘数据管理
│   ├── logger.py              # 日志
│   ├── scheduler.py           # 定时调度
│   ├── doc_parser.py          # 文档解析（多格式 + OCR）
│   ├── doc_parser_v2.py       # 多模态解析编排（MinerU + 回退）
│   ├── mineru_adapter.py      # MinerU 适配器（可选）
│   ├── kg_store.py            # 知识图谱存储（NetworkX + JSONL）
│   ├── ocr.py                 # 图片文字识别
│   └── alert.py               # 告警通知
└── ui/
    ├── server.py              # FastAPI 服务端（55个API端点，含 Chat + KG + 批量问答）
    ├── templates/index.html   # 纯HTML前端（8页面侧边栏）
    └── static/
        ├── style.css          # 样式
        └── commands.js        # 运维命令数据库（138条）
```

## 技术栈

| 组件 | 用途 |
|------|------|
| FastAPI | Web 服务端 |
| Jinja2 | 模板渲染 |
| LangChain | Agent 编排、RAG |
| LangGraph | Agentic RAG ReAct 多步推理 |
| Chroma | 向量存储 |
| NetworkX | 知识图谱存储与查询 |
| sentence-transformers | 文档嵌入 |
| SQLite | 数据持久化 |
| CnOCR | 图片文字识别 |
| Ollama | 本地 LLM 推理 |
| APScheduler | 定时任务 |

## 常见问题

**Q: 需要付费吗？需要 API Key 吗？**
A: 都不需要。默认使用本地 Ollama 大模型，完全免费，数据不出本机。

**Q: 启动后页面空白或加载慢？**
A: 首次运行需下载嵌入模型（约 118MB），等待几分钟即可。后续启动秒开。

**Q: 巡检显示"模拟数据"？**
A: 在 `config.yaml` 中将 `inspection.mode` 改为 `local` 即可使用本机真实检测。

**Q: 可以部署到 Linux 服务器吗？**
A: 可以。安装 Python 3.11+ 和 Ollama，将 `inspection.mode` 改为 `ssh` 并配置目标主机即可。

**Q: 登录认证怎么工作？部署到局域网/公网要注意什么？**
A: 第 5 周起认证实际生效：**本机回环访问（桌面版/本机浏览器）免登录直通；非回环客户端必须登录**
（用 `.env` 的 `OA_AUTH_PASSWORD`，或 `config.yaml` 的 `auth.users` 配置多用户 + `role: admin/viewer`，
密码哈希用 `python scripts/hash_password.py` 生成）。启动带安全门禁：绑定 `0.0.0.0` 等非回环地址时，
未设置非默认密码会**直接拒绝启动**。HTTPS 部署时把 `auth.cookie_secure` 设为 `true`；
细节与实测见 `docs/reports/week5-security-observability-report.md`。

**Q: 启动画面要等一会儿才进入主界面？**
A: 桌面版会等待本地嵌入模型加载完成（首次约 20~40 秒，之后 3~5 秒）。等待超过 8 秒时可点「跳过等待，直接进入」，知识库功能在后台就绪后自动可用。

## License

MIT
