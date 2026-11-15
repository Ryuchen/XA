# 开发环境部署

当前三端开发统一使用本目录的 `dev.ps1`。它绑定现有 `XA-Ubuntu-24.04` WSL2 环境和 `xa-esports-local` Compose 项目；不要再手工启动三套 Windows Vite 或依赖 `.tools` 中的临时服务代码。

在仓库根目录用 PowerShell 7 执行：

```powershell
./deploy/dev.ps1 Up
```

`Up` 会检查 WSL mirrored 网络和 Docker，核对并启动已登记的 MySQL/Redis 容器，检查两店测试数据库及私有配置，启动受控后端，按需通过正常 UAC 配置 hosts，再启动三端容器与网关并检查五个入口。配置不变时重复运行会复用已有进程、容器和 Linux 依赖。

| 操作 | 命令 |
| --- | --- |
| 启动并打开五个入口 | `./deploy/dev.ps1 Up -Open` |
| 查看 WSL 容器、后端进程和域名状态 | `./deploy/dev.ps1 Status` |
| 后端代码修改后重启 | `./deploy/dev.ps1 RestartBackend` |
| 域名/反向代理配置修改后检查并重载 | `./deploy/dev.ps1 Reload` |
| 检查进程、容器、接口、域名和租户绑定 | `./deploy/dev.ps1 Verify` |
| 加做五入口真实浏览器 HMR 验证 | `./deploy/dev.ps1 Verify -Hmr` |
| 查看容器日志与后端日志位置 | `./deploy/dev.ps1 Logs` |
| 只打开预览 | `./deploy/dev.ps1 Open` |
| 停止本套前端、网关和受控后端 | `./deploy/dev.ps1 Down` |

## 固定入口与资源绑定

| 入口 | 本机地址 |
| --- | --- |
| 声浪老板端 | http://sl.esports.club/boss/ |
| 声浪陪玩端 | http://sl.esports.club/player/ |
| 兴安老板端 | http://xa.esports.club/boss/ |
| 兴安陪玩端 | http://xa.esports.club/player/ |
| 统一管理后台 | http://admin.esports.club/ |

资源绑定在 [`esports-local/settings.json`](esports-local/settings.json)：WSL 发行版、Compose 项目、后端端口、现有数据库/Redis 容器及入口列表。可通过 `-Distribution` 指定已经准备好的另一 WSL 发行版，脚本不会自动创建、重装或关闭 WSL。

- 三个 Node 容器运行 `fronted/boss`、`fronted/provider`、`fronted/admin`，内部监听回环 `14101/14102/14103`；Nginx 回环 `80` 按 Host 与路径转发。
- Django/Channels 后端由版本化的 `esports-local/scripts/serve-backend.py` 启动在 Windows 回环 `18106`。后端生命周期由 `backend.ps1` 管理，PID、创建时间、可执行文件、脚本绝对路径与监听均须匹配才能停止。
- `xa-local-validation-db-1` 仅复用 MySQL `127.0.0.1:13306` 中两套独立测试 schema；`xa-fronted-acceptance-redis` 复用 `127.0.0.1:26379`。不启动历史前端、worker/beat，不建库、不迁移、不导入客户或资金。
- 老板/陪玩根据 `sl`、`xa` 域名固定门店。后台通过真实密码和 OTP 识别门店；普通角色只能访问本店，跨店管理要求独立平台授权的超级管理员。

## 开发与配置更新

源码、公共金额模块和 Nginx 配置全部 bind mount；Linux `node_modules` 用 named volumes 与 Windows 依赖隔开。修改前端源码会实时编译并通过 Vite HMR 推送，不需要重建镜像。修改后端代码执行 `RestartBackend`；修改外置 Nginx 配置执行 `Reload`；修改 Compose 或依赖 lockfile 后再运行 `Up`。

首次运行会生成忽略提交的 `esports-local/.env`，自动填入当前 Windows 用户 npm 缓存的 WSL 路径。需要 npm 代理时在该文件设置 `XA_NPM_PROXY=http://127.0.0.1:7897`（仅当该端口确有本机代理）；保留 TLS 校验。已有 `.env` 不会被覆盖。示例见 [`.env.example`](esports-local/.env.example)。

域名配置在 [`esports-local/nginx/conf.d/domains.conf`](esports-local/nginx/conf.d/domains.conf)。改变域名或端口时，须同步 `settings.json`、hosts 脚本和前后端的域名授权绑定；单独修改代理不能改变门店授权。当前为本机 HTTP/WS 预览，未配置公网 DNS 或 HTTPS 证书。

## 首次准备与运行边界

当前部署复用这台开发机已初始化的隔离资源。另一台机器需要先准备 PowerShell 7、WSL2 mirrored 网络及 Docker Engine、仓库 Python `.venv`、上述 MySQL/Redis 容器和已迁移的两个测试库。`.tools/fronted-acceptance/` 仅保留不入库的私有 runtime、微信/SMS 测试传输文件与上传文件；后端代码已迁到 `deploy`。缺少这些资源会明确失败，不自动执行历史 bootstrap 或连接生产。

WSL 的 mirrored 设置属于全机配置，部署只检查，不自动覆盖 `.wslconfig` 或执行 `wsl --shutdown`。hosts 已正确解析时不会再弹 UAC。`Down` 保留 hosts、依赖 volumes、共享 MySQL/Redis 和其他 Compose 项目。未知端口占用、其他 checkout 的同名容器、失配的 PID 记录均拒绝接管或终止。

运行状态与日志位于忽略提交的 `esports-local/.runtime/`，包括 `backend-18106.json`、后端日志和 `deployment-health.json`。浏览器 HMR 验证需要仓库根目录的 Playwright 及其 Chromium；它仅触碰 CSS 修改时间并核对内容散列，不写业务接口。

三端发布 Compose 集中在 [`compose/`](compose/)，静态镜像构建文件位于 [`images/frontend/`](images/frontend/)。发布流程使用 `deploy-release.sh` 和独立环境文件，CI 的远程部署仅在手动触发工作流时运行。`local-validation` 和 `round01` 脚本各有隔离范围，不是当前三端日常开发入口。

2026 年腾讯云旧容器迁移脚本依赖已退役服务、历史备份和固定服务器路径，已从仓库移除。仓库变更不会自动修改任何服务器或已安装的定时任务。完整生产发布、真实微信和短信渠道仍需在目标环境中验收；本机配置检查不代表已经上线。

## 许可与版权

本目录中由青岛声浪传媒有限公司依法享有权利的原创代码、文档与设计属于闭源专有内容，适用仓库根目录的 [LICENSE](../LICENSE)。除适用法律或另行书面授权允许外，不授予使用、复制、修改、部署或分发许可。

第三方组件、字体、图标及其他素材仍适用各自原有许可证和版权声明，参见[第三方说明](../THIRD_PARTY_NOTICES.md)。

Copyright (c) 2026 青岛声浪传媒有限公司. All rights reserved.
