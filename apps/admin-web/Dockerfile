# 运营后台：Vue 3 + Vite SPA。构建静态产物后交由 nginx 托管。

# ---- 构建阶段 ----
FROM node:22-slim AS builder
WORKDIR /app

RUN corepack enable && corepack prepare pnpm@9.0.0 --activate

COPY package.json ./
RUN pnpm install

COPY . .
RUN pnpm run build

# ---- 运行阶段 ----
FROM nginx:alpine
# 作为模板放置：nginx 镜像启动时用 envsubst 注入 BACKEND_ORIGIN，
# NGINX_ENVSUBST_FILTER 限定只替换该变量，保留 $uri/$host 等 nginx 变量。
ENV BACKEND_ORIGIN=http://backend:8000 \
    NGINX_ENVSUBST_FILTER=BACKEND_ORIGIN
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=builder /app/dist /usr/share/nginx/html
EXPOSE 80
