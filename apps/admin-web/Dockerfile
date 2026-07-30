# 运营后台：Vue 3 + Vite SPA。构建静态产物后交由 nginx 托管。

# ---- 构建阶段 ----
# 前端静态资源与 CPU 架构无关，固定构建平台避免多架构发布时重复通过 QEMU 编译。
ARG FRONTEND_BUILDPLATFORM=linux/amd64
FROM --platform=${FRONTEND_BUILDPLATFORM} node:22-slim AS builder
WORKDIR /workspace

RUN corepack enable && corepack prepare pnpm@9.0.0 --activate

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/admin-web/package.json apps/admin-web/package.json
COPY packages/design-system/package.json packages/design-system/package.json
RUN pnpm install --filter xa-admin-web... --frozen-lockfile

COPY apps/admin-web apps/admin-web
COPY packages/design-system packages/design-system
RUN pnpm --filter xa-admin-web run build

# ---- 运行阶段 ----
FROM nginx:alpine
# 作为模板放置：nginx 镜像启动时用 envsubst 注入 BACKEND_ORIGIN，
# NGINX_ENVSUBST_FILTER 限定只替换该变量，保留 $uri/$host 等 nginx 变量。
ENV BACKEND_ORIGIN=http://backend:8000 \
    NGINX_ENVSUBST_FILTER=BACKEND_ORIGIN
COPY apps/admin-web/nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=builder /workspace/apps/admin-web/dist /usr/share/nginx/html
EXPOSE 80
