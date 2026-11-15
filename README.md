# XA 电竞陪玩管理系统

面向电竞陪玩门店的业务管理平台，连接老板下单、陪玩履约与门店运营，覆盖订单、资金、售后、会员活动、客服和经营核算。

**首版开发与验收中 · 三端 Web · 多门店独立数据库 · 闭源专有软件**

三端界面共用 [项目设计原则](DESIGN.md) 与 [系统设计标准](docs/DESIGN_SYSTEM.md)；外部设计参考及适用边界见 [参考库](docs/design/REFERENCE-LIBRARY.md)。

[开发环境](#开发环境) · [安装与验证](#安装与验证) · [项目结构](#项目结构) · [文档导航](#文档导航) · [许可与使用限制](#许可与使用限制)

## 项目概览

项目采用单仓库组织三端网页与后端服务。老板端、陪玩端面向手机触屏与桌面浏览器；管理后台面向桌面及最小 1366 × 1024 CSS 视口的横屏平板。

| 使用端 | 面向角色 | 业务范围 |
| --- | --- | --- |
| 老板端 | 门店客户 | 服务与陪玩浏览、下单与预约、订单进度、评价、钱包及会员权益 |
| 陪玩端 | 陪玩人员 | 抢单与派单、履约与报单、资料与排班、收益、押金及提现 |
| 管理后台 | 门店运营、客服、售后、财务及授权平台人员 | 订单调度、审核与售后、资金与权限管理、运营配置、薪资及经营报表 |
| 后端服务 | 三端与系统任务 | 身份认证、门店隔离、交易记账、业务状态流转、实时通信与异步处理 |

以上为业务覆盖范围；具体实现、待办与验收证据见[开发进度](docs/development/16-开发进度.md)和[需求追踪](docs/development/11-需求与验收追踪.md)。项目当前按首版设计和验收，构建通过不代表所有业务及外部渠道已经完成联调。

## 业务与架构

- **订单与履约**：服务定价、预约、派单与抢单、单陪与双陪、履约凭证、报单及订单房间。
- **资金与售后**：钱包记账、充值、退款、分账结算、押金、提现及对账；资金写入核对权限、幂等、并发和失败回滚。
- **门店与权限**：每店独立业务数据库；域名、身份令牌、接口、文件、缓存、WebSocket 和任务保持门店上下文。平台权限独立于店内角色。
- **运营与经营**：会员、优惠券、活动、礼物、通行证、岗位薪资、每日投流与经营核算。人民币金额与门店虚拟币分别处理。
- **沟通与通知**：咨询、站内消息、订单进度与审核反馈，通过接口和实时通道关联业务状态。

老板端、陪玩端按门店域名进入，不提供跨店切换。管理后台使用统一入口，通过验证后的登录身份确定访问范围；未知域名和未授权身份不能回落到默认门店。

## 技术栈

| 层级 | 技术与职责 |
| --- | --- |
| 三端网页 | React、TypeScript、Tailwind CSS 4、shadcn、Vite |
| 微信授权桥接 | 最小原生小程序包，仅承担微信身份、手机号授权与短信确认 |
| 业务接口 | Python、Django、Django REST Framework |
| 实时与任务 | Channels、Daphne、Celery |
| 数据与缓存 | MySQL 8、Redis |
| 本机开发 | PowerShell 7、WSL2、Docker Compose、Nginx、源码挂载与 HMR |
| 验证工具 | TypeScript、Node.js Test Runner、Django Test Runner、Playwright |

具体依赖版本以各工作区的清单和锁文件为准。三端使用独立 npm workspaces；根目录 pnpm workspace 管理共享金额包与 Playwright 工具。

## 开发环境

### 环境要求

| 组件 | 要求 |
| --- | --- |
| 系统工具 | Windows、PowerShell 7、已准备好的 WSL2 mirrored 网络与 Docker Engine |
| Node.js | 22.12 或更高版本 |
| 包管理器 | 根目录 pnpm 9.0.0；`fronted/` 使用 npm |
| Python | 3.13 或更高版本，仓库 `.venv` 虚拟环境 |
| 数据资源 | 已登记的两店独立测试库、Redis 和私有开发配置 |
| 微信开发者工具 | 需要官方编译验证或授权桥接联调时使用 |

全新开发设备应先按[部署说明](deploy/README.md)准备隔离资源和本机私有配置。标准开发脚本复用已登记的数据库，不自动创建、清空或重建业务库。配置示例见[根环境模板](.env.example)与[本机部署模板](deploy/esports-local/.env.example)；真实密钥和账号由授权维护者单独提供。

### 启动与日常操作

在仓库根目录使用 PowerShell 7：

```powershell
./deploy/dev.ps1 Up
./deploy/dev.ps1 Status
./deploy/dev.ps1 Verify
```

| 操作 | 命令 |
| --- | --- |
| 启动并打开本机入口 | `./deploy/dev.ps1 Up -Open` |
| 后端代码修改后重启 | `./deploy/dev.ps1 RestartBackend` |
| 代理配置修改后检查并重载 | `./deploy/dev.ps1 Reload` |
| 查看日志 | `./deploy/dev.ps1 Logs` |
| 停止本套开发服务 | `./deploy/dev.ps1 Down` |

前端修改通过容器内 Vite HMR 更新。`Down` 保留数据库、Redis、依赖卷和其他项目；完整行为及资源绑定见 [deploy/README.md](deploy/README.md)。

### 本机入口

以下为本机 hosts 与容器代理提供的开发地址，不代表公网服务已发布。

| 角色 | 声浪电竞 | 兴安电竞 |
| --- | --- | --- |
| 老板端 | [sl.esports.club/boss/](http://sl.esports.club/boss/) | [xa.esports.club/boss/](http://xa.esports.club/boss/) |
| 陪玩端 | [sl.esports.club/player/](http://sl.esports.club/player/) | [xa.esports.club/player/](http://xa.esports.club/player/) |
| 管理后台 | [admin.esports.club](http://admin.esports.club/) | 同一入口，由登录身份确定访问范围 |

## 安装与验证

### JavaScript 依赖

首次安装或锁文件变更后，在根目录执行：

```powershell
pnpm install --frozen-lockfile
npm --prefix fronted ci
```

`pnpm-lock.yaml` 与 `fronted/package-lock.json` 分别维护，保留各自的包管理器。

### 前端检查

```powershell
pnpm run typecheck
pnpm run test:frontend
pnpm run bridge:check
pnpm run build:all
```

汇总检查命令为 `pnpm run verify:frontend`，任一步失败即停止。单端构建使用 `pnpm run boss:build`、`pnpm run provider:build` 或 `pnpm run admin:build`。

需要强制使用微信官方编译器验证桥接包时执行：

```powershell
node scripts/check-wechat-bridge.mjs --require-compiler
```

### 后端检查

使用仓库虚拟环境和专用测试配置：

```powershell
./.venv/Scripts/python.exe services/backend/manage.py check --settings=config.test_settings
./.venv/Scripts/python.exe services/backend/manage.py test orders.tests.test_settlement --settings=config.test_settings --noinput
./.venv/Scripts/python.exe services/backend/manage.py makemigrations --check --dry-run --settings=config.migration_test_settings
```

双门店隔离测试示例：

```powershell
./.venv/Scripts/python.exe services/backend/manage.py test stores.tests_admin_entry --settings=config.multitenant_test_settings --noinput
```

以上示例使用一次性 SQLite 测试库。资金与并发改动还需执行相应的隔离 MySQL 测试，微信、短信和支付需分别记录真实渠道与测试替身的覆盖范围。完整规则见[测试、发布与回滚](docs/development/10-测试发布与回滚.md)。

已知失败、跳过项和未覆盖范围保留在对应验证记录中，不能将局部检查通过标记为整体交付完成。最近一次目录与后端整理的结果见[目录清理记录](docs/ops/repository-cleanup-20261111.md)与[后端清理记录](docs/ops/backend-unused-cleanup-20261111.md)。

## 项目结构

```text
XA/
├── fronted/
│   ├── boss/                老板端网页
│   ├── provider/            陪玩工作台
│   ├── admin/               管理后台
│   ├── shared/              会话、接口、实时连接与共享业务模块
│   └── wechat-bridge/       最小微信授权桥接包
├── services/backend/        业务接口、权限、资金、任务、迁移与测试
├── packages/money/          共享金额换算与格式化工具
├── deploy/                  标准开发入口、代理与部署配置
├── scripts/                 验证、隔离联调与运维工具
├── docs/
│   ├── prd/                 产品需求与业务规则
│   ├── development/         研发设计、接口约定、进度与验收追踪
│   ├── ops/                 运维说明与验证记录
│   └── history/             历史文档快照
├── AGENTS.md                项目协作与最新约定
├── CONTRIBUTING.md          开发与提交指南
└── LICENSE                  闭源专有软件声明
```

旧 Vue/Sass 组件包、历史脚本和原型资料保留用于追溯，不参与当前根 workspace 构建。部分历史发布编排仍引用已移除的 `apps/`，生产发布前须单独核对适配情况。

## 文档导航

| 文档 | 用途 |
| --- | --- |
| [项目协作约定](AGENTS.md) | 最新产品形态、门店边界、开发与数据保护约定 |
| [产品需求](docs/prd/README.md) | 业务规则和产品决策 |
| [研发文档](docs/development/README.md) | 架构、事务、接口、工作包和验收追踪 |
| [开发进度](docs/development/16-开发进度.md) | 已实现内容、验证证据与待办 |
| [接口参考](docs/API.md) | 接口说明；字段与权限须结合当前服务端实现核对 |
| [开发环境部署](deploy/README.md) | 环境准备、资源绑定、启动与故障检查 |
| [多门店说明](docs/ops/multitenancy-20261104.md) | 门店初始化、独立数据库和平台授权 |
| [贡献指南](CONTRIBUTING.md) | 协作、检查与提交规范 |

## 协作与发布

开发前核对工作树并保留他人未提交改动，遵循 [AGENTS.md](AGENTS.md) 与[贡献指南](CONTRIBUTING.md)。提交采用 Conventional Commits，说明行为变化、验证结果及未覆盖范围。

首版上线按全新部署准备，仍需完整执行结构迁移、平台与门店初始化及配置验证。线上数据清理和正式发布须由明确授权的独立操作执行；本机日常开发保持已有隔离数据库原状。

真实密钥、验证码、数据库、上传文件、私有 runtime 和客户资料不得提交到仓库或附在公开问题中。缺陷与安全问题通过项目既有的私有协作渠道提交。

## 许可与使用限制

本项目为**闭源专有软件，保留所有权利**。除适用法律或另行书面授权允许的范围外，不授予复制、修改、部署、分发、再许可、公开发布或商业使用本项目原创内容的权利。持有源码、获得仓库访问权限或参与协作不自动构成额外许可，完整条款见 [LICENSE](LICENSE)。

根 JavaScript 包保留 `private: true` 与 `license: "UNLICENSED"` 标记，采用 [npm 对私有包的说明](https://docs.npmjs.com/cli/configuring-npm/package-json/#license)中的配置方式。软件授权范围以本仓库声明及双方另行书面约定为准。

第三方依赖、字体、图标及其他素材仍适用各自许可证，本项目的闭源声明不改变其权利或义务。仓库内已保留 [shadcn-vue 上游许可](packages/shadcn-vue/UPSTREAM-LICENSE)、[Tabler 图标许可](scripts/icon-sources/tabler/LICENSE.txt)及 [MiSans 字体许可](fronted/shared/assets/fonts/misans/MiSans-LICENSE.pdf)；完整位置索引与适配包的许可范围见 [第三方许可说明](THIRD_PARTY_NOTICES.md)。该索引不构成全部第三方组件的许可清单。

Copyright © 2026 青岛声浪传媒有限公司。All rights reserved.
