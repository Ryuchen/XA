# XA 电竞陪玩运营平台

面向电竞陪玩门店的多租户运营平台，连接老板下单、陪玩履约和后台管理，覆盖从订单创建到资金结算的业务流程。

**首版开发中** · 三端 Web · 门店独立数据库 · 平台统一管理

[产品与业务](#产品与业务) · [架构](#架构) · [开始开发](#开始开发) · [项目目录](#项目目录) · [文档](#文档) · [许可](#许可)

> 本项目仍在首版开发和联调阶段。下文介绍产品范围与工程结构，不表示全部功能、外部渠道或生产部署已经完成验收；当前进度以[研发记录](docs/development/16-开发进度.md)为准。

## 产品与业务

| 端 | 使用者 | 主要业务 |
| --- | --- | --- |
| 老板端 | 门店客户 | 浏览服务与陪玩、预约和下单、订单跟进、评价、钱包及会员权益 |
| 陪玩端 | 门店陪玩 | 绑定与资料、抢单、履约与报单、收益、押金及提现 |
| 管理后台 | 平台和门店职能人员 | 门店与授权、订单调度、客服售后、资金审核、运营配置及经营核算 |

系统按门店管理订单、客户、钱包、运营和员工数据。平台超级管理员按授权管理门店和汇总数据；门店人员只访问其所属门店。提现由门店客服初审、平台财务终审并登记线下打款。

## 架构

- **前端**：老板端、陪玩端和管理后台采用 React、TypeScript、Tailwind CSS 4、shadcn 和 Vite；微信授权走公众号网页授权。
- **后端**：Django、Django REST Framework、Channels 和 Celery 提供业务接口、实时通信与后台任务。
- **数据**：MySQL 存储业务数据，Redis 支持缓存、任务与实时通信。平台控制库和各门店业务库的访问边界以当前配置及[多租户方案](docs/development/01-架构权限与多租户.md)为准。
- **部署**：本机开发使用 PowerShell、WSL2、Docker Compose 和 Nginx；发布配置使用 Compose 与 Caddy。

当前门店入口由域名确定，老板端和陪玩端不提供跨店切换；管理后台使用统一入口并根据已验证的身份确定访问范围。接口、令牌、缓存、文件、WebSocket 和异步任务都需要保持门店上下文。

## 开始开发

### 环境要求

| 工具或资源 | 要求 |
| --- | --- |
| 操作系统 | Windows、PowerShell 7、已配置的 WSL2 与 Docker Engine |
| Node.js | 22.12 或更高版本 |
| 包管理器 | 根目录使用 pnpm 9.0.0，`fronted/` 使用 npm |
| Python | 3.13 或更高版本，使用项目独立虚拟环境 |
| 数据服务 | 项目登记的隔离 MySQL 和 Redis 开发资源 |

首次准备开发环境前，请阅读 [deploy/README.md](deploy/README.md)。标准开发脚本复用已登记的隔离数据库，不自动清空或重建业务数据。根目录 [`.env.example`](.env.example) 和 [`deploy/esports-local/.env.example`](deploy/esports-local/.env.example) 是配置模板；真实密钥由授权维护者单独配置。

### 安装依赖

在仓库根目录执行：

```powershell
pnpm install --frozen-lockfile
npm --prefix fronted ci
```

根目录和 `fronted/` 分别使用各自的锁文件。Python 依赖及环境准备见[部署说明](deploy/README.md)。

### 启动本机环境

```powershell
./deploy/dev.ps1 Up
./deploy/dev.ps1 Status
./deploy/dev.ps1 Verify
```

常用操作：

| 操作 | 命令 |
| --- | --- |
| 后端修改后重启 | `./deploy/dev.ps1 RestartBackend` |
| 修改代理配置后重载 | `./deploy/dev.ps1 Reload` |
| 查看日志 | `./deploy/dev.ps1 Logs` |
| 停止本项目开发服务 | `./deploy/dev.ps1 Down` |

`Down` 保留登记的数据库、Redis 和依赖卷。实际启动条件及资源边界见 [deploy/README.md](deploy/README.md)。

### 本机入口

| 门店 | 老板端 | 陪玩端 |
| --- | --- | --- |
| 声浪电竞 | [sl.esports.club/boss/](http://sl.esports.club/boss/) | [sl.esports.club/player/](http://sl.esports.club/player/) |
| 兴安电竞 | [xa.esports.club/boss/](http://xa.esports.club/boss/) | [xa.esports.club/player/](http://xa.esports.club/player/) |

管理后台统一使用 [admin.esports.club](http://admin.esports.club/)。以上地址用于本机开发，不代表公网服务已发布。

### 工程检查

```powershell
pnpm run typecheck
pnpm run test:frontend
pnpm run build:all
./.venv/Scripts/python.exe services/backend/manage.py check --settings=config.test_settings
```

命令适用范围和结果需分别记录。隔离环境检查不能替代真实微信、短信、资金和生产部署验收。

## 项目目录

```text
.
├── fronted/                  三端网页与共享模块
│   ├── boss/                 老板端
│   ├── provider/             陪玩端
│   ├── admin/                管理后台
│   └── shared/               跨端共享模块
├── services/backend/         Django 业务服务、迁移和测试
├── packages/                 共享金额工具
├── deploy/
│   ├── compose/              容器编排
│   ├── esports-local/        本机开发环境与代理
│   └── images/frontend/      三端网页镜像构建
├── scripts/                  工程检查、隔离联调与运维脚本
├── docs/                     产品、研发、架构和运维文档
├── design-demos/             设计资料和原型
├── AGENTS.md                 项目协作与开发约定
└── LICENSE                   项目许可声明
```

旧端脚本及其调用方已逐项核对：无当前调用链的旧脚本和仅依赖旧端的测试已移除；仍与现有业务断言或当前端场景混用的 51 个脚本暂时保留。它们仍依赖已移除的旧端，不能作为当前三端的验收依据。具体处理、测试覆盖变化及剩余调用方见[混合调用复核](docs/ops/records/legacy-script-followup-52-20261115.md)。

## 部署与外部接入

生产配置从 [`deploy/compose/`](deploy/compose/README.md) 和环境变量模板开始。发布工作流的远程部署仅由手动触发；正式发布前需在目标环境完成数据库迁移、平台与门店初始化、授权配置和业务联调。本仓库中的配置检查不构成上线许可。

微信快捷登录和短信服务保留真实接入链路，资质或服务商配置未完成时不能视为已接通。本机隔离替身仅用于开发验证，不能用于生产。

## 文档

| 文档 | 内容 |
| --- | --- |
| [文档中心](docs/README.md) | 文档体系、格式标准、阅读路径与治理规则 |
| [变更日志](CHANGELOG.md) | 全项目变更归纳合并（日期倒序，链接权威记录） |
| [产品需求](docs/prd/README.md) | 三端业务规则和产品决策 |
| [研发方案与进度](docs/development/README.md) | 实施方案、接口契约、工作包和验收追踪 |
| [企业级总体规划](docs/development/26-企业级总体规划.md) | 阶段路线图、验收门禁、生产就绪与治理调整清单 |
| [架构与数据资料](docs/architecture/README.md) | 目录边界、数据模型和流程参考 |
| [开发与部署](deploy/README.md) | 本机资源、启动命令和运行边界 |
| [工程脚本](scripts/README.md) | 场景入口、工程检查和脚本使用范围 |
| [贡献指南](CONTRIBUTING.md) | 项目协作和提交规范 |
| [第三方许可](THIRD_PARTY_NOTICES.md) | 组件、字体和图标的许可及署名 |

## 许可

本项目为闭源专有软件，项目原创代码、文档和设计的使用以 [LICENSE](LICENSE) 及相关书面授权为准。第三方组件和素材适用各自的许可与版权声明，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

Copyright © 2026 青岛声浪传媒有限公司。All rights reserved.
