FROM node:22-slim AS build

ARG XA_APP
WORKDIR /workspace
COPY fronted/package*.json ./fronted/
COPY fronted/admin/package.json ./fronted/admin/
COPY fronted/boss/package.json ./fronted/boss/
COPY fronted/provider/package.json ./fronted/provider/
COPY fronted/shared/package.json ./fronted/shared/
COPY packages/money/ ./packages/money/
RUN cd fronted && npm ci
COPY fronted/ ./fronted/
RUN set -eu; \
    case "$XA_APP" in \
      boss) base=/boss/ ;; \
      provider) base=/player/ ;; \
      admin) base=/ ;; \
      *) echo 'XA_APP must be boss, provider, or admin' >&2; exit 1 ;; \
    esac; \
    cd fronted; XA_BASE_PATH="$base" npm run build --workspace "@xa/fronted-$XA_APP"; \
    mkdir -p /output; \
    case "$XA_APP" in \
      boss) mkdir -p /output/boss; cp -a boss/dist/. /output/boss/ ;; \
      provider) mkdir -p /output/player; cp -a provider/dist/. /output/player/ ;; \
      admin) cp -a admin/dist/. /output/ ;; \
    esac

FROM nginx:alpine
COPY deploy/images/frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /output/ /usr/share/nginx/html/
