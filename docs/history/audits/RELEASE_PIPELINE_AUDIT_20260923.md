# 发布链路验收 · 2026-09-23

> 旧端脚本已删除，历史命令不再适用；具体处理见[脚本核对记录](../../ops/records/legacy-script-audit-20261115.md)。

本文件保留早期发布门禁的阶段记录。后续已安装 WSL、完成容器部署与真实 OpenID 登录，最新证据见 [本地部署验收](../../ops/deployment/LOCAL_DEPLOYMENT_VALIDATION.md)。现已增加托管 MySQL 编排及部署模式选择，部署脚本回归由下文历史 5 项扩展为 7 项通过，见 [托管部署说明](../../ops/deployment/MANAGED_MYSQL_DEPLOYMENT.md)。（微信小程序原生入口与发布检查相关内容已按用户 2026-11-17 决定删除）

## 已修正

1. 陪玩 H5 构建变量接受发布网关的 `/api` 同源代理。
2. Docker 镜像 SHA 标签改为完整提交 SHA，与部署时拉取的标签一致。服务器按该提交检出，适用于分支和标签触发；不再尝试读取不存在的 `origin/<版本标签>`。
3. 自动部署调用 `deploy/deploy-release.sh`，要求 `.env.release` 并使用 remote + release 叠加编排。不会回退开发配置；检查服务器源码是否有改动，不执行强制重置、环境文件改写或全机镜像清理。镜像拉取失败即停止，启动等待之后执行 Django/MySQL 预检。
4. Docker Hub Token 通过 SSH 标准输入用于登录，不拼入远程命令。部署串行排队，新的运行不会取消正在执行的发布。

## 验证证据

- 发布配置检查覆盖发布地址、私有覆盖、环境选择、AppID 与错误退出。
- `node --test scripts/test-deploy-release.mjs`：5 项通过，使用临时本地 Git 仓库及模拟 Docker 命令，验证完整提交部署、源码改动保留、正式环境文件缺失、标签不一致和拉取失败停止。未连接真实远端或启动容器。
- 两个工作流及两个发布编排 YAML 可解析；工作流中 20 个 shell 步骤与部署脚本通过 Bash 语法检查。样式契约通过。
- 对当前工作区运行 release 检查按预期拒绝：对应 API 地址为空、私有域名校验关闭、终端未提供陪玩 H5 API 与环境 AppID。没有填入虚构域名以让检查通过。

## 尚未验证

- 真实服务器 Docker/Compose 启动、迁移、TLS、备份恢复以及公网三端联调。
- 真机的真实微信网页授权登录、手机号授权与客服入口。
- 真实 KOOK 频道、企业微信群及企微坐席投递。
- 提现账户在本轮发布脚本修正后已确认并实现三种方式各保存一个，见 [提现账户专项](WITHDRAWAL_ACCOUNTS_AUDIT_20260923.md)。该项不再是规则阻塞。

配置清单见 [INTEGRATION_CREDENTIALS.md](../../ops/integrations/INTEGRATION_CREDENTIALS.md)，部署步骤见 [RELEASE_READINESS.md](../../ops/deployment/RELEASE_READINESS.md)。

## 提现账户交付后的发布条件复核（安装 WSL 前的历史记录）

2026-09-23 重新执行 `check-release-config.mjs --target=release`，返回退出码 1：实际生效的 `urlCheck` 关闭、目标 API 未填写公网 HTTPS 地址、当前终端缺少陪玩 H5 API 和微信 AppID。没有修改本地联调配置来掩盖这些缺项。

当前工作区没有 `.env.release`、`deploy/.env.release` 或 `services/backend/.env`；终端没有注入微信 AppID/AppSecret、KOOK Token/频道、企微群 Webhook 或三端域名。此检查仅证明当前工作区和进程未提供，不据此推断用户的其他服务器是否已有配置，也没有读取或输出密钥。

本机未找到 Docker 命令；`wsl.exe --list --quiet` 返回“未安装适用于 Linux 的 Windows 子系统”。目前无法在此环境执行真实 Compose 启动。需要用户指定测试或正式服务器、域名及凭据保存位置后继续真实平台联调；参考“小樱电竞”的实际页面证据仍未取得。

## 核对的官方资料

- [Docker metadata-action 的 SHA 标签格式](https://github.com/docker/metadata-action#typesha)：默认短 SHA，完整 SHA 需显式配置 `format=long`。
- [Docker Compose up 参数](https://docs.docker.com/reference/cli/docker/compose/up/)：`--wait` 等待服务运行/健康，`--wait-timeout` 限定等待时间；这不等于已通过业务或微信真机验收。
