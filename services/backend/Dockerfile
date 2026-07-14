# 开发用镜像：安装依赖后由 docker-compose 挂载源码并启动 daphne / celery。
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 系统依赖：Pillow 需要的图形库、MySQL 客户端编译头、curl 供健康检查。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        default-libmysqlclient-dev \
        libjpeg-dev \
        zlib1g-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖，利用镜像层缓存；源码在开发模式下由 compose 卷挂载覆盖。
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# 默认以 daphne 提供 HTTP + WebSocket；worker / beat 在 compose 中覆盖 command。
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "config.asgi:application"]
