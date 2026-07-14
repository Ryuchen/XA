# 贡献指南

本仓库为单仓多端（monorepo）项目，包含四个可部署单元与文档。提交代码前请阅读本指南。

## 仓库结构

```
XA/
├── apps/                     前端可部署单元（纳入 pnpm workspace）
│   ├── boss-miniapp/         老板下单端（Taro + React，小程序 / H5）
│   ├── provider-miniapp/     陪玩接单端（Taro + React，小程序 / H5）
│   └── admin-web/            运营后台（Vue 3 + Vite + Element Plus）
├── services/
│   └── backend/              Django REST / WebSocket / Celery 服务
├── docs/                     产品、接口、数据模型与架构文档
└── archive/                  历史实现，仅供对照，不参与构建
```

## 环境要求

- Node.js `>= 22`（见根 `.nvmrc`），包管理器统一使用 **pnpm 9+**。
- Python 3.13+（后端），MySQL 8、Redis（见根 `docker-compose.yml`）。

## 本地开发

前端（在仓库根执行，pnpm workspace 统一安装依赖）：

```bash
pnpm install
pnpm run admin:dev        # 运营后台开发服务
pnpm run boss:build       # 老板端构建微信小程序
pnpm run provider:build   # 陪玩端构建微信小程序
pnpm run build:all        # 三端全部构建
```

后端：

```bash
cd services/backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # 按需修改
python manage.py migrate
python manage.py runserver
```

## 提交规范

- 遵循 Conventional Commits：`feat:`、`fix:`、`refactor:`、`docs:`、`chore:` 等。
- 每个 PR 聚焦单一目的，附带清晰描述与影响范围。
- 不要提交构建产物、虚拟环境、密钥（`.env`）等文件（见根 `.gitignore`）。

## 分支策略

- `main`：稳定分支，受保护。
- 功能开发从 `main` 切出 `feature/*`，修复用 `fix/*`，通过 PR 合并。
