# OA Ops Agent

基于 LangChain + RAG + Chroma 的 OA 系统智能运维助手，支持自动巡检、日志分析、知识库问答和 AI 报告生成。

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

## 功能

- **巡检监控** — 自动检测端口、服务、磁盘、内存，支持 Local/SSH/Simulated 三种模式，定时调度 + 历史查询
- **日志分析** — 上传或粘贴运维日志，正则匹配 10 种常见故障（502/503/OOM/磁盘满等），输出排查建议
- **知识库问答** — 上传 PDF/Word/TXT 文档，基于 RAG 检索增强生成，严格限制仅基于知识库作答
- **AI 报告** — 巡检完成后自动生成预警分析与改进策略，支持 Ollama 本地离线 / DeepSeek 云端两种后端
- **诊断工具箱** — SSL 证书过期检测、网络诊断（Ping/端口/DNS/路由/HTTP）、数据库巡检（MySQL/MSSQL/Oracle/Redis）、安全基线审计
- **OCR 识别** — 知识库支持导入截图/扫描件自动 OCR；日志分析支持上传报错截图识别后分析

## 快速开始

**无需 API Key，开箱即用。** 默认使用本地离线模式（Ollama），所有数据不出本机。

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
| `OA_AUTH_PASSWORD` | Web 登录密码（v2.2 暂未启用） | `admin123` |
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
├── agents/                    # Agent 模块（9个）
│   ├── inspection_agent.py    # 巡检（模拟 + 统一入口）
│   ├── inspection_real.py     # 真实巡检（SSH + Local）
│   ├── log_analysis_agent.py  # 日志分析（正则规则库）
│   ├── knowledge_agent.py     # 知识库 RAG
│   ├── ai_reporter.py         # AI 报告生成
│   ├── ssl_monitor.py         # SSL 证书监控
│   ├── network_diag.py        # 网络诊断
│   ├── db_inspector.py        # 数据库巡检
│   └── security_audit.py      # 安全基线检查
├── utils/                     # 基础设施
│   ├── config.py              # 配置管理
│   ├── database.py            # SQLite 持久化
│   ├── logger.py              # 日志
│   ├── scheduler.py           # 定时调度
│   ├── doc_parser.py          # 文档解析（含OCR）
│   ├── ocr.py                 # 图片文字识别
│   └── alert.py               # 告警通知
└── ui/
    ├── server.py              # FastAPI 服务端（36个API端点）
    ├── templates/index.html   # 纯HTML前端（6页面侧边栏）
    └── static/style.css       # 样式
```

## 技术栈

| 组件 | 用途 |
|------|------|
| FastAPI | Web 服务端 |
| Jinja2 | 模板渲染 |
| LangChain | Agent 编排、RAG |
| Chroma | 向量存储 |
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

**Q: 如何修改默认密码？**
A: v2.2 暂未启用 Web 登录认证。如需启用，可在 `.env` 中设置 `OA_AUTH_PASSWORD=你的新密码`，并在 `ui/server.py` 中添加认证中间件。

## License

MIT
