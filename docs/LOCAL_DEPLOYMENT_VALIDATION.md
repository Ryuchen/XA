# WSL 本地部署验收记录

验收日期：2026-09-23。当前工作区已在本机 WSL 2 中构建并运行完整容器环境，使用真实 MySQL、Redis、Daphne、Celery、Caddy、管理端和陪玩 H5。老板端使用微信开发者工具运行原生小程序，已连接此后端。

## 访问与账号

| 入口 | 本机地址 |
| --- | --- |
| API | http://127.0.0.1:18080/api/ |
| 管理端 | http://127.0.0.1:18081 |
| 陪玩端 H5 | http://127.0.0.1:18082 |
| 老板原生小程序 | 微信开发者工具导入 `apps/boss-miniapp`，原生目录 `native/` |

管理体验账号为 `local_admin`，陪玩体验账号为 `local_provider`。随机密码保存在未纳入 Git 的 `.tools/local-review-accounts.json`；只用于本机测试，不能移用于生产。老板端已通过真实微信登录建立本地测试账号，余额初始为零，可由本地管理端按测试充值流程操作。

价目表已导入 **572 项**，其中 **464 项上架**，并复制 28 个引用封面、导入 4 篇说明。1 元 = 10 兴安币 = 100 内部账务单位。服务目录按同一玩法合并展示，价格档位在详情内选择。验收专用商品已下架，测试订单、资金流水和凭证保留供复核。

## 已通过的实际检查

| 项目 | 结果及证据 |
| --- | --- |
| Linux 与 Docker | Ubuntu 24.04.5 LTS / systemd，官方 hello-world 运行通过 |
| 三个业务镜像 | 当前工作区的后端、管理端、陪玩 H5 均在 Linux 构建成功 |
| 数据库 | 完整迁移通过，含 wallet.0027_saved_withdrawal_accounts；READ COMMITTED、InnoDB、命名时区转换检查通过 |
| 托管库部署模式 | 独立编排连接另一 Compose 项目的 TLS MySQL；完整迁移、错误证书/主机名拒绝、56 项关键回归通过，见 [托管数据库专项](MANAGED_MYSQL_DEPLOYMENT.md)；未连接腾讯云 |
| 私有图片与客服权限 | 三端网关拒绝凭证静态直链，授权访问/过期续签/公开封面通过；无聊天权限员工不接收会话，管理端实际预览通过，见 [访问专项](PRIVATE_MEDIA_AUDIT_20260923.md) |
| MySQL 全量回归 | 托管库配置、凭证权限及实时授权修复后的 1003 项，991 通过、12 项按环境跳过；包含完整迁移、独立连接并发和资金流程。网关源码白名单检查在 Windows 通过，容器以实际 HTTP 检查补充 |
| HTTP 业务闭环 | 实际网关上的充值、指定陪玩、优先抢单、后台派单、进场凭证、结单凭证、报单、审核分账、取消、退款全部通过 |
| 资金与提现 | 充值重试不重复入账；审核前陪玩不入账；每单流水合计为零；三类收款账户独立保存；解绑后原提现快照保留；通过与驳回的可用额、冻结额正确 |
| 原生页面代码 | 原生页面控制器通过真实容器 HTTP 执行下单、已提交但响应丢失后的重试恢复、取消、履约、评价、聊天和试音身份切换 |
| 真实微信开发者工具 | 实际 wx.login → 后端 → 微信 code2Session 成功；未绑定识别、本地新账号建立、重复快捷登录识别同一账号均通过，模拟登录关闭 |
| 原生界面 | 实际微信模拟器截图验证首页、四类筛选、服务合并展示、内部套餐切换；切换套餐保持当前页面栈 |
| 管理端浏览器 | Chromium 登录、概览、服务、派单、报单、提现页面通过 |
| 陪玩端浏览器 | Chromium 手机视口登录、订单及提现账户页通过，三类绑定入口正常，无横向溢出；两端检查未发现接口或脚本运行错误 |
| WebSocket | Windows 经网关连接 /ws/orders/ 与 /api/ws/orders/，心跳及 Redis 分组推送通过；匿名连接被拒绝 |
| 断线与恢复 | 49 项客户端回归通过；停止单 Web 时 57 次读取零错误；双 Web 停机恢复后三端自动重连并重读数据，聊天草稿保留，H5 退出登录关闭连接，见 [恢复专项](REALTIME_RECOVERY_AUDIT_20260923.md) |
| 登录会话隔离 | 新增 40 项会话检查，客户端组合回归 89 项通过；两端实际 HTTP 凭证刷新、退出后迟到刷新拒绝、失效刷新单次退出通过，镜像已更新，见 [会话专项](AUTH_SESSION_AUDIT_20260923.md) |
| 已建立连接的权限变更 | 封禁陪玩、停用后台成员和令牌到期后在下次心跳/推送前断开；有效刷新令牌可恢复连接，失效会话只退出一次。新增后端 12 项、相关客户端 55 项通过，实际网关复验及镜像更新完成，见 [实时权限专项](LIVE_AUTHORIZATION_AUDIT_20260923.md) |
| 本机短时负载 | 最新实时权限修复后复验：100 老板 + 60 陪玩、160 条 WebSocket；160 人阶段 2,936 请求零错误，p95 730 ms；10 单幂等、退币和通知核对通过。读取延迟与云端持续负载目标仍待验收，见 [容量记录](LOCAL_CAPACITY_VALIDATION.md) |
| 同单并发抢单 | 60 人同时争抢，1 人成功、59 人业务冲突；数据库只有一份有效接单关系，回池取消后余额与流水正确 |
| 异步任务 | Worker 心跳通过；真实 Redis 队列 → Worker → Redis 结果链路执行超时扫描任务通过；只运行一个 Beat |
| 持久化 | 停止容器并重启 WSL 后，余额、3 个已结算订单、流水、提现收款快照、10 份上传凭证及 572 项价目完整保留 |
| 备份恢复 | 冷备应用数据库及媒体，在新库和独立媒体目录恢复；67 张表、1,361 条记录、54 个文件的结构和内容摘要一致，原应用库与媒体卷保留 |
| 空闲可用性 | 修复 WSL 自动停止后，仅从 Windows 连续 90 秒、6 次访问均成功，发行版启动时间保持不变 |

HTTP 验收标识：`fc7b87aea986`。老板账号 ID 1、陪玩账号 ID 2、已结算订单 ID 1/2/3、已通过提现 ID 1；结束时余额分别为 40,000 / 38,000 内部账务单位（4,000 / 3,800 兴安币），冻结额均为 0。提现打款编号以 `LOCAL_SIMULATED_PAYOUT_` 开头，未发生真实转账。

11 项跳过分别为 10 项 SQLite 价目导入夹具检查，以及 1 项需要 Node 的原生控制器 HTTP 检查（生产后端镜像没有 Node）。价目导入保护和价目规则另有 12 项本机回归通过；原生 HTTP 验证器的 Django LiveServer 回归 1 项及对真实部署网关的控制器验证也已单独通过。不同运行环境的结果不相加为 MySQL 覆盖率。

首次全量 MySQL 回归 948 项暴露了提现账户编辑与提现申请之间的死锁。已统一为“本人钱包 → 收款记录”的锁定顺序；新增两个确定性交错测试，在旧实现上均复现 MySQL 1213，修复后该模块 12 项全部通过，再执行当时的 950 项全量回归成功。后续查询优化新增 5 项检查，完整回归增至上述 955 项。修改或解绑不改变已提交提现的快照，也不会重复冻结资金。详见 [提现账户记录](WITHDRAWAL_ACCOUNTS_AUDIT_20260923.md)。

修复镜像重新部署后的 HTTP 复验标识为 `595059bd4066`：老板账号 11、陪玩账号 12、结算订单 8/9/10、通过提现 3；余额分别为 40,000 / 38,000，冻结额均为 0。验收商品、陪玩与占位客服已隐藏，历史业务记录保留。

双 Web 实例复验标识为 `bc59b2365fcc`：老板账号 974、陪玩账号 975、结算订单 84/85/86、通过提现 5；完整流程通过，余额分别为 40,000 / 38,000，冻结额均为 0。当前共有 9 个运行容器（含两个 Web 和一个 Beat）。压力场景、修复对照和边界见 [本地容量验证](LOCAL_CAPACITY_VALIDATION.md)。双实例部署后真实微信登录继续识别原账号，原生首页、四类服务和内部套餐切换也已复验。

## 运行方式

Windows PowerShell：

```powershell
wsl -d XA-Ubuntu-24.04 -u root --cd /mnt/g/Github/XA --exec bash deploy/local-validation.sh up
wsl -d XA-Ubuntu-24.04 -u root --cd /mnt/g/Github/XA --exec bash deploy/local-validation.sh status
wsl -d XA-Ubuntu-24.04 -u root --cd /mnt/g/Github/XA --exec bash deploy/local-validation.sh stop
```

`stop` 保留全部命名卷。不要使用 `down -v`。脚本同时支持 `config`、`build`、`check`、`smoke`、`test`、`test-all`：

- `config` 核对真实 Compose 合并结果中的数据库和本机端口，不输出密钥。
- `build` 串行构建三个镜像；镜像名 `xa-local/*:validation`，没有推送镜像。
- `check` 核对 Django、MySQL 和 Worker。
- `smoke` 新建独立测试身份和商品，通过真实 HTTP 运行业务流程。保留可复核的本地测试记录，不连接支付或外部消息平台。
- `test` 运行关键专项，`test-all` 运行全部后端测试。只授权和使用 `xa_test_container`、`test_xa_test_container` 两个测试库，不清空应用库。使用内存队列、通知层及进程临时媒体目录，避免与应用的 Worker、实时通知及持久化上传目录互相干扰；真实 Redis 链路另行验收。

后续修改后端需重新构建镜像并执行 `up`，容器没有挂载源码。老板端开发配置已指向 18080；体验版和正式版 HTTPS 地址仍需部署时填写。

## 本地备份恢复演练

复现命令：

```powershell
wsl -d XA-Ubuntu-24.04 -u root --cd /mnt/g/Github/XA --exec bash deploy/local-recovery-drill.sh
```

脚本先校验本地 Compose 和测试库状态，短暂停止网关、Web、Worker、Beat，备份 MySQL 与只读媒体卷并生成清单，然后恢复应用服务。SQL 恢复仅写入新建的 `xa_restore_<时间>_<随机值>` 数据库，媒体解压到独立目录；通过每张表的结构、逐行摘要和每个文件的长度、SHA-256 比对恢复结果。备份及恢复文件保留在 WSL root 专用目录，不写入 Git，不覆盖或删除源库和媒体卷。

2026-09-23 实测结果：67 张表、1,361 条记录、54 个媒体文件全部一致。备份目录 `/var/lib/xa-local-validation/recovery/run-20260923T094117Z-rUktch`，恢复库 `xa_restore_20260923094117_cc88d33c`，私有目录内的 `result.json` 和工作区 `.tools/wsl-setup/recovery-drill.log` 保存结果。备份包含测试账号和凭证，应保持此目录的受限权限。

演练后 Windows 访问 API、管理端、陪玩端均返回 200；原订单余额、提现快照、10 份业务凭证和 572 项目录仍正确。真实 Redis → Worker → 结果查询通过，8 个服务运行、迁移任务退出 0；日志 `.tools/wsl-setup/post-recovery-check.log`。

这是本机数据库与媒体的一致性恢复验证，不包含 Redis 队列、异地 COS 备份或云数据库时间点恢复；不据此宣称云端 RPO/RTO 已达标。正式云环境仍须按部署方案演练。

## 本机环境与网络修复

- WSL 2.7.14.0，Linux 内核 6.18.33.2-microsoft-standard-WSL2。
- 发行版 XA-Ubuntu-24.04，存放于 G:\WSL\XA-Ubuntu-24.04；项目路径 /mnt/g/Github/XA。
- Docker Engine 29.8.1、Compose 5.5.1、Buildx 0.37.1。
- Ubuntu 包取自官方源，SHA-256 `bb415d824822c4b878125729af451a5d18fb13d1cf5cbed9a7393ad64ac6039e` 与 Microsoft WSL 发行版清单一致。
- .wslconfig 限制 4 GB 内存、4 个处理器、4 GB swap，使用 mirrored 网络。

Docker Hub 直连曾出现 TLS 超时，已为专用 Ubuntu 的 Docker 配置现有本机代理 127.0.0.1:7897，没有修改 Windows 代理或防火墙。构建中临时 EOF 保留缓存后重试已恢复，没有关闭证书校验或换用第三方镜像。后续构建需要此代理运行；已构建服务的启动不依赖镜像下载。

WSL 空闲自动停止曾导致页面断连，现已在用户级 .wslconfig 添加 `[general] instanceIdleTimeout=-1`，保存旧配置后停止容器并重启 WSL，未重启 Windows。该值依据 [Microsoft WSL 配置说明](https://learn.microsoft.com/en-us/windows/wsl/wsl-config)，连续空闲访问复验通过。需要释放资源时先执行 stop，再自行关闭 WSL。

WSL 中构建时使用：

```sh
export HTTP_PROXY=http://127.0.0.1:7897
export HTTPS_PROXY=http://127.0.0.1:7897
export NO_PROXY=localhost,127.0.0.1,::1,web,db,redis,gateway,admin-web,provider-h5
bash deploy/local-validation.sh build
```

## 隔离与微信凭据

Compose 项目固定 xa-local-validation，数据库 xa_verify 使用非 root 的同名应用账号。数据库、Redis 不映射宿主端口；只有网关的 18080/18081/18082 绑定 127.0.0.1。.tools/local-validation.env 使用随机本地密码并被 Git 忽略。

用户提供的 AppID 与小程序项目一致，微信官方稳定 token 接口验证返回错误码 0。AppSecret 保存在 WSL /etc/xa-local-validation/wechat.env，目录 root 700、文件 root 600。Windows 仅保留 .tools/wechat.local.env.dpapi 当前用户加密备份，工作区明文副本已删除。脚本自动叠加 deploy/compose.local-wechat.yml，仅在后端运行时注入，未写入前端或镜像。

本地价目导入仅允许 DEBUG SQLite，或者同时满足显式本地标志、MySQL、数据库 xa_verify、主机 db、账号 xa_verify 的隔离容器，不会写远程或生产库。

## 仍需上线环境验收

本次没有验收真实手机号授权和旧手机号账号合并、手机真机、微信合法域名、腾讯云公网 TLS、KOOK 频道实际消息、企业微信客服卡片或真实提现打款。真实微信登录和 OpenID 绑定已通过，不应因此宣称手机号绑定也已通过。

腾讯云主机、数据库、DNS 与备案仍按 docs/TENCENT_DEPLOYMENT_PLAN.md 后续落实；KOOK、企微所需项见 docs/INTEGRATION_CREDENTIALS.md。本机关闭了 KOOK/企微实际群消息发送。已完成 100 老板与 60 陪玩的本机短时协议负载及 60 人争抢同单验证；正式云端 50 RPS / 30 分钟基线与容量余量测试仍未执行。

构建、回归及启动日志和截图位于 .tools/wsl-setup/。最新结果为 mysql-capacity-full.log、capacity-two-web.log、grab-race-two-web.log、two-web-final-smoke.log、wechat-after-capacity.log、native-after-capacity.log。提现并发修复、备份恢复以及此前原生/两端浏览器截图和日志仍保留。容量结构化结果在 .tools/capacity/。查询 WebSocket 服务访问日志时应遮蔽 URL 中的登录 token。


## 2026-09-23 会员运营增量

已部署 engagement 0001–0005、coupons 0006–0007 与 orders 0036，执行隔离目录导入。管理端会员运营权限已按用户明确授权添加到本地测试角色。新增 15 礼物素材、会员/积分/转盘/站内播报及活动海报内容。9 个容器运行（含 2 个 Web）。

本轮 MySQL 全量 1030 项（1018 通过、12 跳过），SQLite 1030 项（1010 通过、20 跳过）；赠礼权益补充 3 项通过。原生 46 页通过官方编译，28 项核心交互通过，开发者工具与管理端完成显示核验。最新截图在 `.tools/engagement-native/`。

本地抽奖仍关闭，未确定的赠礼附送规则仍停用。活动入口采用“近期活动/下单须知弹窗、首页单条热点、倒序活动列表、纯图片详情”，既有 VIP 权益不再用作入场弹窗。详见 [会员运营说明](CLUB_ENGAGEMENT_IMPLEMENTATION.md)。
