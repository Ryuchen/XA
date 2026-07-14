# XA 游戏陪玩管理系统

单仓多端（monorepo）项目：老板下单小程序、陪玩接单小程序、运营后台，以及统一的 Django REST / WebSocket 后端服务。

## 仓库结构

```
XA/
├── apps/                     前端可部署单元（pnpm workspace）
│   ├── boss-miniapp/         老板下单端（Taro 4 + React 18，微信小程序 / H5）
│   ├── provider-miniapp/     陪玩接单端（Taro 4 + React 18，微信小程序 / H5）
│   └── admin-web/            运营后台（Vue 3 + Vite + Element Plus）
├── services/
│   └── backend/              Django、订单状态机、计价结算、钱包、运营接口
├── docs/                     产品、接口、数据模型与架构文档
├── archive/                  历史实现，仅供对照，不参与当前构建或部署
├── docker-compose.yml        本地 MySQL / Redis
└── .github/workflows/        CI（前端构建 + 后端 check）
```

生成的 `node_modules/`、`dist/`、`dist-h5/`、`services/backend/venv/` 及运行期媒体文件均不属于源码，已在根 `.gitignore` 中排除。

## 环境要求

- Node.js `>= 22`（见根 `.nvmrc`），包管理器统一使用 **pnpm 9+**。
- Python 3.13+、MySQL 8、Redis。

## 本地启动

### 方式一：Docker Compose 一键启动后端全栈（推荐）

复制 `.env.example` 为 `.env`，然后启动全部后端服务（MySQL、Redis、Django Web、Celery worker/beat）：

```bash
cp .env.example .env
docker compose up -d --build
```

启动后自动完成数据库迁移，后端可通过 `http://127.0.0.1:8000` 访问。常用操作：

```bash
docker compose logs -f web                        # 查看 Web 日志
docker compose exec web python manage.py createsuperuser
docker compose down                               # 停止（保留数据卷）
```

compose 管理的服务：`db`、`redis`、`migrate`（一次性迁移）、`web`（daphne，HTTP + WebSocket）、`celery-worker`、`celery-beat`。开发模式下 `services/backend/` 源码以卷挂载，改动即时生效。

### 方式二：仅用容器起基础设施，本地跑后端

```bash
docker compose up -d db redis
cd services/backend
cp .env.example .env
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

### 前端（在仓库根统一安装依赖）

```bash
pnpm install
pnpm run admin:dev        # 运营后台开发服务
pnpm run boss:build       # 老板端构建微信小程序（输出 apps/boss-miniapp/dist/）
pnpm run provider:build   # 陪玩端构建微信小程序
pnpm run build:all        # 三端全部构建
```

老板端 `apps/boss-miniapp/` 的构建产物按平台隔离：微信小程序输出到 `dist/`，H5 输出到 `dist-h5/`。微信开发者工具应导入 `apps/boss-miniapp/project.config.json` 所在目录，不要直接导入 `dist-h5/`。

## 容器化部署

### 后端全栈

见上文「Docker Compose 一键启动后端全栈」。

### 前端三端（各自独立镜像，便于分开部署）

三端各带独立 `Dockerfile` 与 `nginx.conf`，均为多阶段构建（node 构建静态产物 → nginx 托管），可单独构建、单独部署：

```bash
# 运营后台（Vue SPA）
docker build -t xa-admin-web ./apps/admin-web
docker run -d -p 8080:80 -e BACKEND_ORIGIN=http://后端地址:8000 xa-admin-web

# 老板端 H5（build:h5）
docker build -t xa-boss-h5 ./apps/boss-miniapp
docker run -d -p 8081:80 -e BACKEND_ORIGIN=http://后端地址:8000 xa-boss-h5

# 陪玩端 H5（build:h5）
docker build -t xa-provider-h5 ./apps/provider-miniapp
docker run -d -p 8082:80 -e BACKEND_ORIGIN=http://后端地址:8000 xa-provider-h5
```

`BACKEND_ORIGIN` 为后端地址（默认 `http://backend:8000`），nginx 启动时注入，负责把 `/api`、`/media`、`/ws` 反代到后端，实现同源访问。

> 注意：两个小程序端的 **微信小程序形态无法容器化运行**，容器化的是它们的 **H5 形态**。需要小程序时仍执行 `pnpm run boss:build` / `pnpm run provider:build` 并导入微信开发者工具。

### 一键启动「后端 + 三端 H5/Web」

```bash
cp .env.example .env
docker compose -f docker-compose.web.yml up -d --build
```

启动后访问：运营后台 `http://127.0.0.1:8080`、老板端 H5 `http://127.0.0.1:8081`、陪玩端 H5 `http://127.0.0.1:8082`，后端 `http://127.0.0.1:8000`。

## 文档

- 产品现状、三端功能与业务规则：[docs/PRODUCT_DOCUMENTATION.md](docs/PRODUCT_DOCUMENTATION.md)
- 技术边界与端到端流转：[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- 贡献与分支规范：[CONTRIBUTING.md](CONTRIBUTING.md)

## 许可

私有软件（UNLICENSED），保留所有权利。详见 [LICENSE](LICENSE)。
