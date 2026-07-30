# 陪玩端：微信小程序 + H5 双形态工程。
# 小程序端（build:weapp）无法容器化运行，需导入微信开发者工具；
# 本镜像仅容器化 H5 形态：构建静态站点后交由 nginx 托管。

# ---- 构建阶段 ----
# Taro 4.1.9 未发布 Linux ARM64 binding。静态资源与 CPU 架构无关，
# 因此固定在 AMD64 构建，再复制到目标架构的 nginx 运行镜像。
ARG FRONTEND_BUILDPLATFORM=linux/amd64
FROM --platform=${FRONTEND_BUILDPLATFORM} node:22-slim AS builder
WORKDIR /workspace

# 构建期注入后端 API 地址；默认同源 /api，由 nginx 反代到后端。
ARG TARO_APP_API=/api
ENV TARO_APP_API=${TARO_APP_API}

RUN corepack enable && corepack prepare pnpm@9.0.0 --activate

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/provider-miniapp/package.json apps/provider-miniapp/package.json
COPY packages/design-system/package.json packages/design-system/package.json
RUN pnpm install --filter xa-provider-miniapp... --frozen-lockfile

COPY apps/provider-miniapp apps/provider-miniapp
COPY packages/design-system packages/design-system
# 陪玩端 H5 产物输出到 dist（见 config/index.ts）。
RUN pnpm --filter xa-provider-miniapp run build:h5

# ---- 运行阶段 ----
FROM nginx:alpine
# 作为模板放置：nginx 镜像启动时用 envsubst 注入 BACKEND_ORIGIN，
# NGINX_ENVSUBST_FILTER 限定只替换该变量，保留 $uri/$host 等 nginx 变量。
ENV BACKEND_ORIGIN=http://backend:8000 \
    NGINX_ENVSUBST_FILTER=BACKEND_ORIGIN
COPY apps/provider-miniapp/nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=builder /workspace/apps/provider-miniapp/dist /usr/share/nginx/html
EXPOSE 80
