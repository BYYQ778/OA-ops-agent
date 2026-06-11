# ============================================================
# OA运维多智能Agent巡检问答系统 - Docker 镜像
# ============================================================
# 构建: docker build -t oa-ops-agent .
# 运行: docker-compose up -d
# ============================================================

FROM python:3.11-slim

LABEL maintainer="DB" \
      description="OA运维多智能Agent巡检问答系统" \
      version="2.2"

# 设置工作目录
WORKDIR /app

# 安装系统依赖（chromadb 需要 sqlite3）
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        sqlite3 \
        procps \
        net-tools \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖清单并安装 Python 包
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    --trusted-host pypi.tuna.tsinghua.edu.cn \
    && pip install --no-cache-dir paramiko \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    --trusted-host pypi.tuna.tsinghua.edu.cn

# 复制项目文件
COPY . .

# 创建数据目录
RUN mkdir -p data/inspection_logs data/chroma_db

# 暴露端口
EXPOSE 7860

# 环境变量
ENV PYTHONUNBUFFERED=1

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860')" || exit 1

# 默认启动完整模式
CMD ["python", "main.py", "--host", "0.0.0.0"]
