# 陪玩端：微信小程序 + H5 双形态工程。
# 小程序端（build:weapp）无法容器化运行，需导入微信开发者工具；
# 本镜像仅容器化 H5 形态：构建静态站点后交由 nginx 托管。

# ---- 构建阶段 ----
FROM node:22-slim AS builder
WORKDIR /app

# 构建期注入后端 API 地址；默认同源 /api，由 nginx 反代到后端。
ARG TARO_APP_API=/api
ENV TARO_APP_API=${TARO_APP_API}

RUN corepack enable && corepack prepare pnpm@9.0.0 --activate

COPY package.json ./
RUN pnpm install

COPY . .
# 陪玩端 H5 产物输出到 dist（见 config/index.ts）。
RUN pnpm run build:h5

# ---- 运行阶段 ----
FROM nginx:alpine
# 作为模板放置：nginx 镜像启动时用 envsubst 注入 BACKEND_ORIGIN，
# NGINX_ENVSUBST_FILTER 限定只替换该变量，保留 $uri/$host 等 nginx 变量。
ENV BACKEND_ORIGIN=http://backend:8000 \
    NGINX_ENVSUBST_FILTER=BACKEND_ORIGIN
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=builder /app/dist /usr/share/nginx/html
EXPOSE 80
