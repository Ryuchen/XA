# 本机三端域名容器预览

后续开发统一执行 `./deploy/dev.ps1 Up`。完整启停、WSL 资源绑定、后端重启和检查命令见 [开发环境部署](../README.md)。本目录的 `up.ps1`、`down.ps1`、`reload-nginx.ps1` 已转为统一入口的兼容包装。

入口：

- 声浪老板端：http://sl.esports.club/boss/
- 声浪陪玩端：http://sl.esports.club/player/
- 兴安老板端：http://xa.esports.club/boss/
- 兴安陪玩端：http://xa.esports.club/player/
- 统一后台：http://admin.esports.club/

`esports.club` 只返回入口指引及 404，不默认绑定门店。未知 Host 被拒绝。域名不在公网发布；Windows hosts 将以上四个域名映射到本机 IPv4 回环。后台通过账号密码与短信验证码识别所属店铺；新账号首次登录还须完成微信 H5 绑定初始化。已绑定微信可在微信内快捷登录，普通角色不可切店，授权超管管理门店；更换店内身份需重新登录。微信开放平台扫码入口仅保留待开发组件。

## 本机前提

目前使用 `XA-Ubuntu-24.04` 的 Linux Docker Engine、WSL2 mirrored 网络，以及更新代码的 Windows 隔离后端 `127.0.0.1:18106`。原 18105 服务与 13101/13102/13103 预览均保留。Compose 不启动数据库、迁移、种子、生产后端或消息任务。WSL root 仅用于操作现有 Docker daemon，不修改用户组或系统安全策略。

当隔离测试资源已存在时，在仓库根目录启动整个环境：

```powershell
./deploy/dev.ps1 Up
```

后端代码已迁入 `scripts/serve-backend.py`，由 `backend.ps1` 隐藏启动和校验进程归属，不再依赖 `.tools` 中的临时启动代码。此步骤只启动既有测试资源上的服务，不执行 bootstrap 或迁移。

## 启停

在 PowerShell 中运行：

```powershell
./deploy/esports-local/up.ps1
```

启动脚本仅在域名未正确解析时请求正常 UAC；管理员脚本先备份 hosts，只处理四个指定域名，结果存入本目录 `.runtime/hosts-result.json`。机上 Windows PowerShell 5 的 Restricted 策略会拒绝脚本，因此辅助程序使用当前 PowerShell 7（RemoteSigned），不修改执行策略。若取消 UAC，可自行在管理员 PowerShell 7 中运行 `install-hosts.ps1`，然后再次 `Up`。

首次启动会在 Linux named volumes 中执行 `npm ci`；后续按 lockfile、Node 版本和 CPU 架构复用依赖。Windows `node_modules` 被根及各 workspace 的 named volumes 全部遮蔽。Windows npm cache 只读挂载，仅复制内容到 Linux `npm_cache`。此机 npm 直连曾被网络重置，忽略提交的 `.env` 使用现有本机 HTTP CONNECT 代理 `127.0.0.1:7897`；可参考 `.env.example` 调整，不关闭 TLS 校验、不修改 lockfile。

```powershell
./deploy/esports-local/reload-nginx.ps1
./deploy/esports-local/down.ps1
```

停止脚本只停止当前 Compose 项目和脚本持有归属记录的后端，并保留依赖 volumes、共享数据库/Redis及hosts。后端代码修改后执行 `./deploy/dev.ps1 RestartBackend`。

## 外置配置与热更新

`nginx/nginx.conf` 和 `nginx/conf.d/` 均以只读 bind mount 注入 Nginx。域名、入口、后端地址在 `nginx/conf.d/domains.conf` 修改后运行 reload 脚本；先通过 `nginx -t` 才重载。

`fronted/`、`packages/money/` 挂载源码，三个独立 Node 容器分别运行 Vite：`/boss/`、`/player/` 以及后台 `/`。Vite 使用文件轮询，保存源码即可编译并通过同域 WebSocket 推送 HMR。后端 `/api/`、`/ws/`、`/media/` 直接转 18106，不经过 Vite。

这是实际运行 Vite 的源码热编译，不是打包静态页面后挂载。运行 `node deploy/esports-local/verify-hmr.cjs` 可通过真实域名读取五个入口，再仅 touch 已导入 CSS 的 mtime，保存实际 Vite WebSocket `update` 消息及前后内容 SHA256；脚本不修改或恢复源码内容，不提交业务操作。验证文件位于 `.runtime/hmr-results.json`、`.runtime/mounts.json`；网关的重定向、未知 Host、回环监听验证位于 `.runtime/gateway-results.json`。

四个运行容器使用 Linux host network，以兼容 mirrored 下仅监听 Windows loopback 的隔离后端。Nginx 只监听 `127.0.0.1:80`；Vite 只监听 Linux `127.0.0.1:14101/14102/14103`，不暴露到局域网。切换到普通 Docker bridge 或其他机器时，应明确重新配置后端可达地址，不可直接把监听改成公网。

反代始终传递匹配服务器的原始 `Host`，清除客户端 `X-Forwarded-Host`；不根据客户端任意 slug 决定门店。后端和共享前端的域名权威绑定由其业务代码负责，冲突门店上下文必须拒绝。修改域名时须同步上述权威绑定及 hosts，不只修改 Nginx。

## 许可与版权

本目录中由青岛声浪传媒有限公司依法享有权利的原创代码、文档与设计属于闭源专有内容，适用仓库根目录的 [LICENSE](../../LICENSE)。除适用法律或另行书面授权允许外，不授予使用、复制、修改、部署或分发许可。

第三方组件、字体、图标及其他素材仍适用各自原有许可证和版权声明，参见[第三方说明](../../THIRD_PARTY_NOTICES.md)。

Copyright (c) 2026 青岛声浪传媒有限公司. All rights reserved.
