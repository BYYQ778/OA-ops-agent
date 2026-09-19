# ============================================================
# OA运维多智能Agent巡检问答系统 - Docker 镜像
# ============================================================
# 构建: docker build -t oa-ops-agent .
# 运行: docker-compose up -d
# ============================================================

FROM ghcr.io/astral-sh/uv:0.12.3 AS uv

FROM python:3.12-slim AS core

COPY --from=uv /uv /usr/local/bin/uv

LABEL maintainer="DB" \
      description="OA运维多智能Agent巡检问答系统" \
      version="3.0.1"

# 设置工作目录
WORKDIR /app

# 基础巡检工具；OCR 所需系统库在完整 runtime 阶段安装。
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        sqlite3 \
        procps \
        net-tools \
    && rm -rf /var/lib/apt/lists/*

# 锁文件是容器与本地开发的共同依赖来源。core 可用于无模型 API 冒烟。
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

# 复制项目文件
COPY . .

# 创建数据目录
RUN mkdir -p data/inspection_logs data/chroma_db

# 暴露端口
EXPOSE 7860

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/api/health', timeout=3)" || exit 1

# 默认启动完整模式
CMD ["python", "main.py", "--host", "0.0.0.0"]

# 默认完整 Web 镜像。不包含 Windows 桌面壳，不在构建时下载模型权重。
FROM core AS runtime
RUN apt-get update && \
    apt-get install -y --no-install-recommends libgomp1 libgl1 && \
    rm -rf /var/lib/apt/lists/*
RUN uv sync --locked --no-dev --no-install-project --extra rag --extra ocr
