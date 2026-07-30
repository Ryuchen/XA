# 兴安电竞（XA）游戏陪玩平台

XA 是一套单仓多端的游戏陪玩业务系统，覆盖老板下单、陪玩抢单与履约、运营派单与审核、钱包结算、消息通知、签到和礼物打赏等完整流程。

项目包含四个运行单元：

- **老板端**：微信小程序，老板快捷登录后选择游戏、服务项目、陪玩和客服并下单。
- **陪玩端**：H5 / Web，陪玩抢单、开始服务、完成服务、报单、回复评价、管理资料和申请提现。
- **管理端**：Web 运营后台，管理老板、陪玩、订单、派单、审核、充值、钱包、通行证、签到奖励、消息和基础配置。
- **服务端**：Django REST Framework + Channels + Celery，统一承载业务接口、WebSocket、异步任务、MySQL 和 Redis。

> 业务金额统一以“兴安币”展示和存储，换算比例为 **1 元人民币 = 10 兴安币**。例如签到门槛 188 元和 388 元分别对应 1,880 和 3,880 兴安币。

## 目录结构

```text
XA/
├── apps/
│   ├── boss-miniapp/         # 老板微信小程序（Taro + React）
│   ├── provider-miniapp/     # 陪玩 H5 / Web（Taro + React，目录名为历史命名）
│   └── admin-web/            # 运营管理后台（Vue 3 + Vite + Element Plus）
├── services/
│   └── backend/              # Django REST / Channels / Celery 服务
├── docs/                     # 产品、接口、架构、数据模型和设计规范
├── archive/                  # 历史代码，仅供对照，不参与构建
├── docker-compose.yml        # 后端、MySQL、Redis、Celery 编排
├── docker-compose.web.yml    # 后端与三端 Web 镜像的整合编排（见部署说明）
└── pnpm-workspace.yaml       # 前端 workspace 定义
```

## 核心业务

### 老板端

- 微信快捷登录并匹配原有账号。
- 按“游戏类目 → 游玩项目 → 指定/不指定陪玩 → 负责客服”完成自助下单。
- 查看订单状态、评价已完成订单并快捷选择礼物打赏。
- 浏览陪玩列表、陪玩排行榜和礼物专区；陪玩支持按游戏筛选。
- 通过企业微信客服联系充值或客服下单，充值金额按 1:10 转为兴安币。
- 消息中心、优惠券、余额及充值记录、常用游戏资料管理。
- 月度签到：当日消费满 1,880 兴安币完成签到，满 3,880 兴安币额外获得补签卡；补签卡最多 3 张，每月重置，满签奖励可由后台配置。

### 陪玩端

- 抢单池展示订单金额、预计到手金额和老板备注要求。
- 订单状态按“待抢单 → 已接单 → 服务中 → 待报单/审核 → 已完成”流转。
- 通行证分级抢单：黑卡立即可见，金卡延迟 30 秒，银卡延迟 60 秒，铜卡延迟 120 秒，无卡最后可见。
- 通行证按日购买：黑卡 500、金卡 300、银卡 100、铜卡 20 兴安币/天；购买 30 天按总价 95 折。
- 完成服务后从订单生成报单，上传入队截图、结单截图及多张战绩截图。
- 查看老板评价并回复；管理个人信息、语音卡和服务资料。
- 钱包统一展示余额、收入、礼物、奖惩、通行证消费和提现记录，并支持申请提现。
- 接收运营后台发送的系统消息和订单消息。

### 管理端

- 老板、陪玩、员工及权限管理。
- 游戏类目、服务项目、陪玩资料、陪玩等级和陪玩排行榜管理。
- 订单、派单、状态流转、报单审核、评价和礼物打赏管理。
- 钱包流水、兴安币充值、提现审核、奖惩和通行证管理。
- 签到规则、补签卡、满签奖励、优惠券、公告和系统消息配置。
- 客服名片、企业微信入口及试听链接等运营配置。

## 技术栈与环境要求

| 组件 | 技术 | 本地端口 |
| --- | --- | --- |
| 老板端 | Taro 4、React 18、TypeScript、Sass | H5 调试建议 `3000` |
| 陪玩端 | Taro 4、React 18、TypeScript、Sass | H5 调试建议 `3001` |
| 管理端 | Vue 3、Vite 5、Element Plus、Pinia | `5180` |
| 服务端 | Django、DRF、Channels、Daphne、Celery | `8000` |
| 基础设施 | MySQL 8、Redis | `3306`、`6379` |

本地开发建议安装：

- Node.js `>= 22`（见 `.nvmrc`）
- pnpm `9.0.0`（见根 `package.json#packageManager`）
- Docker Desktop 与 Docker Compose v2
- 微信开发者工具（仅老板微信小程序构建需要）

```bash
corepack enable
corepack prepare pnpm@9.0.0 --activate
pnpm install
```

## 快速启动

### 1. 准备环境变量

```bash
cp .env.example .env
```

`.env` 只用于本机或部署环境，不要提交真实密码和微信 `AppSecret`。

如需把待派订单推送到 KOOK 频道，在 `.env` 中配置机器人 Token 和文字频道 ID：

```dotenv
KOOK_ENABLED=true
KOOK_BOT_TOKEN=机器人Token
KOOK_DISPATCH_CHANNEL_ID=目标文字频道ID
KOOK_DISPATCH_NEW_ORDERS=true
KOOK_DISPATCH_REJECTED_ORDERS=true
# 可选：通知指定角色、增加运营后台跳转按钮
KOOK_DISPATCH_MENTION_ROLE_IDS=角色ID1,角色ID2
KOOK_ADMIN_ORDER_URL=https://admin.example.com/orders
```

机器人需要先加入目标 KOOK 服务器并拥有频道发消息权限。新建后未指定陪玩的订单，以及陪玩拒单后重新回到待接单池的订单，会通过 Celery 异步发送卡片；发送结果可在 Django Admin 的 `Kook dispatch records` 中查询。

### 2. 启动后端服务

```bash
docker compose up -d --build
docker compose ps
```

该命令会启动 MySQL、Redis、数据库迁移、Django/Daphne、Celery Worker 和 Celery Beat。接口默认位于 `http://127.0.0.1:8000/api`。

常用命令：

```bash
docker compose logs -f web
docker compose exec web python manage.py migrate
docker compose exec web python manage.py test --noinput
docker compose down
```

除非确认可以丢弃本地业务数据，否则不要执行 `docker compose down -v`。

### 3. 启动三个前端

分别打开三个终端，在仓库根目录运行：

```bash
# 老板端 H5，仅用于浏览器联调；正式交付形态是微信小程序
pnpm --filter xa-boss-miniapp exec taro build --type h5 --watch --port 3000

# 陪玩 H5 / Web
pnpm --filter xa-provider-miniapp exec taro build --type h5 --watch --port 3001

# 运营管理后台
pnpm run admin:dev
```

对应访问地址：

- 老板端 H5：`http://127.0.0.1:3000`
- 陪玩端：`http://127.0.0.1:3001`
- 管理端：`http://127.0.0.1:5180`
- 后端接口：`http://127.0.0.1:8000/api`

## 微信小程序构建与登录

### 构建

```bash
pnpm run boss:build
```

在微信开发者工具中导入 `apps/boss-miniapp`。项目配置的 `miniprogramRoot` 为 `dist/`，因此必须先完成构建，确保 `apps/boss-miniapp/dist/app.json` 已生成。不要直接把 `dist` 目录当作项目根目录导入。

### 登录配置

真实微信快捷登录需要在 `.env` 中配置与 `apps/boss-miniapp/project.config.json` 一致的 AppID：

```dotenv
WECHAT_APPID=微信小程序AppID
WECHAT_SECRET=微信小程序AppSecret
WECHAT_MOCK_LOGIN=false
```

修改后重建或重启后端：

```bash
docker compose up -d --build web celery-worker celery-beat
```

后端登录匹配顺序为：

1. 优先按微信 `openid` 匹配已绑定老板账号；
2. 未绑定时，在已验证且手机号唯一的情况下绑定原账号；
3. 无匹配账号时才创建新老板账号；
4. 手机号冲突时拒绝自动绑定，交由运营人员处理。

`WECHAT_MOCK_LOGIN=true` 只适合本地 H5 联调，生产或微信开发者工具真机联调必须关闭，否则无法验证真实微信身份，也可能产生非预期测试账号。

陪玩开始服务、完成服务时默认必须上传入队和结单截图。旧版客户端灰度升级期间可临时设置
`REQUIRE_ORDER_EVIDENCE_IMAGES=false`，生产稳定后应恢复为 `true`；即使关闭该开关，报单正式提交仍会校验完整凭证。

## 测试账号与阶段数据

后端提供可重复执行的联调数据命令。重跑只清理 `demo_*` 测试账号及其关联数据，不会清理手工创建的正常账号。

```bash
docker compose exec web python manage.py seed_stage_test_data
docker compose exec web python manage.py seed_gift_test_data
```

默认生成：

| 用途 | 账号 | 密码 | 说明 |
| --- | --- | --- | --- |
| 管理端客服 | `demo_stage_operator` | `test1234` | 全阶段运营账号 |
| 陪玩端 | `demo_provider_01` ～ `demo_provider_20` | `test1234` | 前 5 个为可抢单状态，覆盖各类通行证 |
| 老板数据 | `demo_boss_01` ～ `demo_boss_20` | `test1234` | 用于后台和订单数据验证；老板端仍以微信登录为准 |

全阶段数据包含老板、陪玩、商品、订单状态、抢单、服务、报单、评价、消息、钱包、充值、提现、奖惩、优惠券及通行证记录。可自定义数量和密码：

```bash
docker compose exec web python manage.py seed_stage_test_data \
  --customers 20 --providers 20 --password test1234
```

## 常用构建与检查

```bash
# 正式老板微信小程序
pnpm run boss:build

# 浏览器交付构建
pnpm run boss:build:h5
pnpm run provider:build:h5
pnpm run admin:build

# 后端测试
docker compose exec web python manage.py test --noinput

# 检查编排文件语法
docker compose config --quiet
docker compose -f docker-compose.web.yml config --quiet
```

根脚本 `provider:build` 和 `build:all` 中仍保留陪玩微信小程序构建，用于历史兼容；当前产品交付应使用 `provider:build:h5`。

## Docker 部署说明

### 已验证范围

根 `docker-compose.yml` 可用于一键启动开发后端栈：MySQL、Redis、迁移任务、Web、Celery Worker 和 Celery Beat。

### 整合 Web 编排

`docker-compose.web.yml` 计划提供以下地址：

- 管理端：`http://127.0.0.1:8080`
- 老板 H5：`http://127.0.0.1:8081`
- 陪玩 H5：`http://127.0.0.1:8082`
- 后端：`http://127.0.0.1:8000`

启动命令为：

```bash
docker compose -f docker-compose.web.yml up -d --build
```

### 自动构建镜像

`.github/workflows/docker-publish.yml` 在 Pull Request 中执行多架构构建校验，在合并到
`main`、推送 `v*` 标签或手动触发时，将以下 AMD64/ARM64 镜像发布到 GHCR：

- `ghcr.io/ryuchen/xa-backend`：Django / Daphne / Celery 共用后端镜像。
- `ghcr.io/ryuchen/xa-dispatch`：运营派单后台。
- `ghcr.io/ryuchen/xa-provider`：陪玩端 H5。

分支、版本、Git SHA 和 `latest` 标签由工作流自动生成；发布镜像同时附带 SBOM、OCI
元数据和构建来源证明。工作流使用仓库自带的 `GITHUB_TOKEN`，无需额外配置镜像仓库密码。

目前整合编排仍属于**开发/验收配置**，不能直接视为生产部署方案。生产上线前至少还应使用非
root 数据库账号、关闭 Django Debug、限制 Allowed Hosts/CORS、配置 HTTPS 与微信合法域名、
托管静态与媒体文件、备份数据库并接入日志和监控。

## 接口与实时通信

- 普通业务接口前缀：`/api`
- 管理端接口前缀：`/api/admin`
- 媒体文件：`/media`
- 订单与消息实时更新：Django Channels WebSocket

具体字段、权限和流程请以 [接口文档](docs/API.md) 与服务端路由为准。

## 常见问题

### 前端页面没有数据

先确认后端及依赖服务状态：

```bash
docker compose ps
curl -i http://127.0.0.1:8000/api/users/me/
docker compose logs --tail=200 web
```

`401 Unauthorized` 通常表示后端已响应但当前未登录；连接拒绝或 `5xx` 才需要继续检查容器、数据库迁移和服务日志。

### 微信快捷登录每次都创建新账号

检查 `WECHAT_APPID`、`WECHAT_SECRET` 是否与小程序一致，并确认 `WECHAT_MOCK_LOGIN=false`。真实环境还需确认原老板账号已保存正确 `openid`，或具备唯一且已验证的手机号供首次绑定。

### 微信开发者工具提示找不到 `dist/app.json`

在仓库根执行 `pnpm run boss:build`，确认构建成功后重新导入 `apps/boss-miniapp`。

### MySQL 报 `Access denied` / `1045`

MySQL 密码只在数据卷首次初始化时生效。已有 `mysql_data` 数据卷时，单纯修改 `.env` 不会修改数据库中的密码。优先恢复原密码或在数据库内安全修改用户密码；不要为了修复登录问题直接删除数据卷。

### 端口被占用

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
lsof -nP -iTCP:3000 -sTCP:LISTEN
lsof -nP -iTCP:3001 -sTCP:LISTEN
lsof -nP -iTCP:5180 -sTCP:LISTEN
```

结束旧进程或为当前开发服务指定其他端口。

## 项目文档

- [产品需求文档](docs/PRD.md)
- [代码现状版产品文档](docs/PRODUCT_DOCUMENTATION.md)
- [系统架构与业务边界](docs/ARCHITECTURE.md)
- [接口文档](docs/API.md)
- [数据模型 ER 图](docs/ER.md)
- [核心流程时序图](docs/Sequence.md)
- [三端视觉规范](docs/DESIGN_SYSTEM.md)
- [贡献指南](CONTRIBUTING.md)

## 提交规范

- 使用 Conventional Commits，例如 `feat:`、`fix:`、`refactor:`、`docs:`、`chore:`。
- 不提交 `.env`、AppSecret、数据库密码、构建产物、虚拟环境或本地日志。
- 每个 PR 聚焦一个清晰目标，并在提交前完成对应端构建和受影响的后端测试。

本项目为私有业务项目，未经授权不得分发或用于其他用途。
