# 本地离线 AI 部署指南 — 内网环境适用

## 为什么用 Ollama？

内网/涉密环境不能连外网 → 无法调用 DeepSeek API → 需要用本地大模型。

Ollama 是最简单的本地 LLM 方案：
- 一键安装，零配置
- API 与 OpenAI 完全兼容 → 现有代码不用改
- 支持国产模型：qwen2.5 / deepseek-r1

## 部署步骤

### 1. 在能上网的机器上下载

```powershell
# 下载 Ollama 安装包
# https://ollama.com/download/windows
# 安装后会自动启动服务（端口 11434）

# 拉取模型（选一个）
ollama pull qwen2.5:7b        # 推荐：中文强，4.7GB
ollama pull deepseek-r1:8b    # 推理强，4.9GB
ollama pull llama3:8b         # 通用强，4.7GB
```

### 2. 离线传输到内网机器

```
将以下目录拷贝到内网机器相同路径：
  C:\Users\<用户名>\.ollama\
```

然后在内网机器上安装 Ollama（离线安装包可在 ollama.com 下载）。

### 3. 修改项目配置

```yaml
# config.yaml
llm:
  provider: ollama             # 改为 ollama
  ollama:
    base_url: http://localhost:11434/v1
    model: qwen2.5:7b          # 你拉取的模型名
```

### 4. 重启服务

```powershell
python main.py --demo
```

## 验证

浏览器点「巡检本机」，看到报告头显示 `Ollama/qwen2.5:7b (本地离线)` 即成功。

## 推荐模型

| 模型 | 大小 | 中文 | 推理 | 适用场景 |
|------|------|------|------|----------|
| qwen2.5:7b | 4.7GB | ⭐⭐⭐ | ⭐⭐ | 巡检报告/中文分析 |
| deepseek-r1:8b | 4.9GB | ⭐⭐ | ⭐⭐⭐ | 深度推理/根因分析 |
| qwen2.5:14b | 8.5GB | ⭐⭐⭐ | ⭐⭐⭐ | 高质量报告（需16G内存） |
