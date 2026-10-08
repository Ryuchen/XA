# 原生入口与发布链路验收 · 2026-09-23

本文件保留早期发布门禁的阶段记录。后续已安装 WSL、完成容器部署与开发者工具真实 OpenID 登录，最新证据见 [本地部署验收](LOCAL_DEPLOYMENT_VALIDATION.md)。现已增加托管 MySQL 编排及部署模式选择，部署脚本回归由下文历史 5 项扩展为 7 项通过，见 [托管部署说明](MANAGED_MYSQL_DEPLOYMENT.md)。

## 已修正

1. 老板端统一以 `apps/boss-miniapp/native/` 为交付入口，修正文档中遗留的 `dist/` 导入说明、旧导航及登录描述。结构检查拒绝项目重新指向历史 Taro 目录。
2. 原生 `trial` 与 `release` 分别检查，不回退其他环境。要求 HTTPS API、合法的 AppID 匹配及实际生效的域名校验开关；私有项目设置覆盖公共设置也会被检查。WebSocket 必须为 WSS 根地址，原生运行时去除末尾斜杠，避免拼出双斜杠路径。
3. 原生配置与陪玩 H5 构建变量明确分开。H5 接受发布网关的 `/api` 同源代理，原生必须填完整公网 API；旧老板 H5 的媒体/socket 变量不再被当作原生配置。
4. `build:release` 和老板端 `deploy:weapp` 要求正式配置与官方 WXML/WXSS 编译器通过；普通开发检查仍允许无编译器时只检查结构。所有这些命令都不上传小程序。
5. Docker 镜像 SHA 标签改为完整提交 SHA，与部署时拉取的标签一致。服务器按该提交检出，适用于分支和标签触发；不再尝试读取不存在的 `origin/<版本标签>`。
6. 自动部署调用 `deploy/deploy-release.sh`，要求 `.env.release` 并使用 remote + release 叠加编排。不会回退开发配置；检查服务器源码是否有改动，不执行强制重置、环境文件改写或全机镜像清理。镜像拉取失败即停止，启动等待之后执行 Django/MySQL 预检。
7. Docker Hub Token 通过 SSH 标准输入用于登录，不拼入远程命令。部署串行排队，新的运行不会取消正在执行的发布。

## 验证证据

- `node --test scripts/test-native-core.cjs scripts/test-release-config.mjs`：35 项通过。覆盖原生业务控制器和发布地址、私有覆盖、环境选择、AppID、错误退出及强制官方编译器路径。
- `node scripts/check-native.mjs --require-compiler`：40 个页面、41 个 WXML 模板、42 个 WXSS 样式通过，原生源包约 386 KB。
- `node --test scripts/test-deploy-release.mjs`：5 项通过，使用临时本地 Git 仓库及模拟 Docker 命令，验证完整提交部署、源码改动保留、正式环境文件缺失、标签不一致和拉取失败停止。未连接真实远端或启动容器。
- 两个工作流及两个发布编排 YAML 可解析；工作流中 20 个 shell 步骤与部署脚本通过 Bash 语法检查。样式契约通过。
- 对当前工作区运行 release / trial 检查均按预期拒绝：对应 API 地址为空、私有域名校验关闭、终端未提供陪玩 H5 API 与环境 AppID。没有填入虚构域名以让检查通过。

## 尚未验证

- 真实服务器 Docker/Compose 启动、迁移、TLS、备份恢复以及公网三端联调。
- 微信开发者工具与真机的真实登录、手机号授权、客服入口、体验版上传和正式审核。
- 真实 KOOK 频道、企业微信群及企微坐席投递。
- 提现账户在本轮发布脚本修正后已确认并实现三种方式各保存一个，见 [提现账户专项](WITHDRAWAL_ACCOUNTS_AUDIT_20260923.md)。该项不再是规则阻塞。

配置清单见 [INTEGRATION_CREDENTIALS.md](INTEGRATION_CREDENTIALS.md)，部署步骤见 [RELEASE_READINESS.md](RELEASE_READINESS.md)。

## 提现账户交付后的发布条件复核（安装 WSL 前的历史记录）

2026-09-23 重新执行 `check-release-config.mjs --target=trial` 与 `--target=release`，二者均返回退出码 1：实际生效的 `urlCheck` 关闭、目标原生 API 未填写公网 HTTPS 地址、当前终端缺少 `TARO_APP_API` 和 `WECHAT_APPID`。没有修改本地联调配置来掩盖这些缺项。

当前工作区没有 `.env.release`、`deploy/.env.release` 或 `services/backend/.env`；终端没有注入微信 AppID/AppSecret、KOOK Token/频道、企微群 Webhook 或三端域名。此检查仅证明当前工作区和进程未提供，不据此推断用户的其他服务器是否已有配置，也没有读取或输出密钥。

本机未找到 Docker 命令；`wsl.exe --list --quiet` 返回“未安装适用于 Linux 的 Windows 子系统”。目前无法在此环境执行真实 Compose 启动。需要用户指定测试或正式服务器、域名及凭据保存位置后继续真实平台联调；参考“小樱电竞”的实际页面证据仍未取得。

## 核对的官方资料

- [Docker metadata-action 的 SHA 标签格式](https://github.com/docker/metadata-action#typesha)：默认短 SHA，完整 SHA 需显式配置 `format=long`。
- [Docker Compose up 参数](https://docs.docker.com/reference/cli/docker/compose/up/)：`--wait` 等待服务运行/健康，`--wait-timeout` 限定等待时间；这不等于已通过业务或微信真机验收。
