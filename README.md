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

### 1. 基础设施

复制 `.env.example` 为 `.env`，启动 MySQL 与 Redis：

```bash
docker compose up -d
```

### 2. 后端

```bash
cd services/backend
cp .env.example .env
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

### 3. 前端（在仓库根统一安装依赖）

```bash
pnpm install
pnpm run admin:dev        # 运营后台开发服务
pnpm run boss:build       # 老板端构建微信小程序（输出 apps/boss-miniapp/dist/）
pnpm run provider:build   # 陪玩端构建微信小程序
pnpm run build:all        # 三端全部构建
```

老板端 `apps/boss-miniapp/` 的构建产物按平台隔离：微信小程序输出到 `dist/`，H5 输出到 `dist-h5/`。微信开发者工具应导入 `apps/boss-miniapp/project.config.json` 所在目录，不要直接导入 `dist-h5/`。

## 文档

- 产品现状、三端功能与业务规则：[docs/PRODUCT_DOCUMENTATION.md](docs/PRODUCT_DOCUMENTATION.md)
- 技术边界与端到端流转：[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- 贡献与分支规范：[CONTRIBUTING.md](CONTRIBUTING.md)

## 许可

私有软件（UNLICENSED），保留所有权利。详见 [LICENSE](LICENSE)。
