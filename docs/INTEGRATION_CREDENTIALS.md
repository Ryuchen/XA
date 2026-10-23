# 上线对接凭证与配置清单

更新：2026-09-23。按当前源码、环境变量模板和 GitHub Actions 工作流整理。表中只有变量名称，没有真实密钥。将生产值保存在部署环境或平台的 Secrets 中，前端构建参数只能包含公开标识和地址。

尚未开通 KOOK 或微信客服时，先按 [开通指引](KOOK_WECOM_SETUP.md) 创建机器人、客服账号并完成小程序绑定，再填写本文配置。

## 1. 当前发布方案必须准备

| 对接项 | 要准备的内容 | 项目配置位置 | 获取或准备方式 |
| --- | --- | --- | --- |
| 微信老板小程序 | AppID（公开标识） | 服务端 `WECHAT_APPID`；`apps/boss-miniapp/project.config.json` 的 `appid`；发布构建时两者必须一致 | 微信公众平台的小程序账号，开发管理中的开发设置 |
| 微信登录与手机号授权 | AppSecret（密钥） | **仅服务端** `WECHAT_SECRET` | 同一小程序的开发设置，由有权限的管理员获取或重置 |
| 服务端签名 | Django Secret Key（随机密钥） | `DJANGO_SECRET_KEY` | 生成独立的高强度随机值；当前业务 JWT 默认也使用此签名配置，轮换需要考虑现有登录会话 |
| 应用数据库 | 库名、非 root 用户名、密码 | `MYSQL_DATABASE`、`MYSQL_USER`、`MYSQL_PASSWORD`；托管方式另配 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_SSL_CA_FILE` | 数据库管理员创建应用账号并授权给项目库；内置库使用 remote + release，托管库单独使用 `docker-compose.managed.yml` |
| 自建 MySQL 初始化 | root 密码 | `MYSQL_ROOT_PASSWORD` | 仅用于数据库初始化和运维，应用使用上面的非 root 密码；改环境变量不会自动修改已有库密码 |
| 正式访问地址 | API、后台、打手 H5 三个域名 | `XA_API_DOMAIN`、`XA_ADMIN_DOMAIN`、`XA_PROVIDER_DOMAIN` | 准备域名、DNS 和服务器；使用 HTTPS/WSS |
| 原生老板端访问后端 | API、可选独立 WebSocket 根地址 | `apps/boss-miniapp/native/config.js` 的 `trial` / `release` 中 `apiBase`、`socketBase` | API 如 `https://api.<域名>/api`；socket 如 `wss://api.<域名>`，留空从 API 推导；媒体根地址也由 API 推导。`TARO_APP_*` 不会注入原生文件 |
| 陪玩 H5 访问后端 | API 地址 | 构建环境 `TARO_APP_API` | 发布编排使用 `/api` 同源反代，也可单独构建为完整 HTTPS API；WebSocket 从该地址及当前站点推导。`TARO_APP_WS` / `TARO_APP_MEDIA` 仅控制历史老板 Taro/H5 实现 |

正式模板为 [`deploy/release.env.example`](../deploy/release.env.example)。微信访问、上传、下载与 socket 合法域名、隐私保护指引和真机授权也要在平台配置，不能用 Token 代替这些步骤。生产必须关闭 `WECHAT_MOCK_LOGIN`。

托管 MySQL 使用 [`deploy/managed.env.example`](../deploy/managed.env.example)，需要数据库平台签发的 CA 公钥证书，应用不需要数据库 root 密码或服务器证书私钥；启用方式见 [托管部署说明](MANAGED_MYSQL_DEPLOYMENT.md)。

试音入口还使用 `AUDITION_BOSS_LINK_BASE_URL`、`AUDITION_PROVIDER_LINK_BASE_URL`（旧版统一配置为 `AUDITION_LINK_BASE_URL`）。应指向实际部署的对应 H5 试音页面，如 `https://provider.<域名>/#/pages/audition/index`，并按实际路由模式核对；老板正式形态是小程序，如需浏览器试音链接须另部署老板 H5。试音的 `boss_token` / `provider_token` 由后台建链接时自动生成，不需要手工准备，但链接属于受限访问凭证，应仅交给预定使用人。

**无需手工准备的临时 Token：**微信 `access_token` 由服务端使用 AppID/AppSecret 获取；微信登录 `code`、手机号授权 `code` 由客户端授权流程取得；业务登录 access/refresh JWT 由 XA 服务端签发。不要把这些短期值当成永久配置填入 `.env`。

## 2. KOOK 频道派单

| 内容 | 配置 | 是否敏感 / 用途 |
| --- | --- | --- |
| 机器人 API Token | `KOOK_BOT_TOKEN` | **密钥**；在 KOOK 开发者中心创建机器人的配置中取得，仅由后端和 Worker 使用 |
| 目标文字频道 ID | `KOOK_DISPATCH_CHANNEL_ID` | 标识；机器人应加入该频道所属服务器并获准发消息 |
| 启用开关 | `KOOK_ENABLED=true` | 非密钥；未配置时后台复制文案仍可用 |
| 新单、拒单回池通知 | `KOOK_DISPATCH_NEW_ORDERS`、`KOOK_DISPATCH_REJECTED_ORDERS` | 非密钥，按运营需要启停 |
| 要提醒的角色 ID（可选） | `KOOK_DISPATCH_MENTION_ROLE_IDS` | 逗号分隔的角色 ID，取自目标服务器 |
| 后台订单链接（可选） | `KOOK_ADMIN_ORDER_URL` | 正式后台 `/orders` 地址，接收人仍须正常登录 |
| 请求超时（可选） | `KOOK_REQUEST_TIMEOUT=8` | 单次请求超时秒数，默认 8 秒 |

服务器 `.env.release` 中填写这些参数，按数据库模式使用 remote + release 或独立 managed 编排；KOOK 配置通过共享环境传给 Web、Celery Worker 和 Beat。修改配置后应重新创建相关容器，仅重启旧容器不会刷新 Compose 环境变量。确保 Redis、Worker、Beat 正常，后端可访问 KOOK HTTPS API；机器人须进入目标服务器，能查看目标文字频道、发送消息，需要提醒角色时还须具备对应权限。

自动发送的范围是已支付、未过期、未指派的单陪待接订单，以及符合条件的拒单回池订单。已指派、双陪、礼物订单不广播到抢单频道。后台“订单 → 发单”可查看文案、渠道配置状态及发送记录。一个订单的同一拒单轮次只创建一条渠道发送记录；明确失败可重试，状态未知时应先到频道核查，避免重复发单。卡片展示冻结的打手实得和玩法要求，数量单位来自下单快照。

当前只向 KOOK 发派单卡片，使用 HTTP Bot 鉴权和频道消息接口。官方参考：[API 鉴权](https://developer.kookapp.cn/doc/reference)、[频道消息](https://developer.kookapp.cn/doc/http/message)。这里不需要 KOOK OAuth Client Secret，也不需要接收事件用的 Verify Token。

若以后增加“在 KOOK 内点击抢单、接收群指令、绑定身份组”，才需要新增接收事件及账号绑定业务：机器人事件接收方式、公开回调地址、Verify Token、启用加密时的 Encrypt Key，以及目标服务器/身份组 ID。**这些接口尚未实现，也没有对应的生产配置项。** Verify Token 与 Bot API Token 是两种凭证，参考 [KOOK Webhook 文档](https://developer.kookapp.cn/doc/webhook)。

## 3. 企业微信群机器人派单

| 内容 | 配置 | 获取方式 |
| --- | --- | --- |
| 完整群机器人 Webhook | `WECOM_DISPATCH_WEBHOOK` | 在目标企业微信群的机器人管理中添加机器人，复制该群的 Webhook |
| 启用开关 | `WECOM_DISPATCH_ENABLED=true` | 非密钥 |
| 自动派单范围 | `WECOM_DISPATCH_NEW_ORDERS`、`WECOM_DISPATCH_REJECTED_ORDERS` | 非密钥，控制新单与拒单回池通知 |

Webhook 形如 `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...`。**整条地址视为密钥**，其中的 `key` 不单独另填一个 Token。当前后端校验官方 HTTPS 域名与路径，不把地址下发到浏览器。

这是企业微信群机器人，不是普通个人微信群接口，也不是微信客服会话入口。官方入口：[群机器人配置](https://developer.work.weixin.qq.com/document/path/91770)。配置和发送状态说明见 [GROUP_DISPATCH.md](GROUP_DISPATCH.md)。

## 4. 企业微信原生客服接待（当前已接入的方式）

| 内容 | 配置位置 | 说明 |
| --- | --- | --- |
| 企业 ID / CorpID | 管理后台 → 客服名片 → `wecom_corp_id` | 企业微信“我的企业”中的企业 ID，属于公开标识 |
| 官方客服接入链接 | 同一名片 → `wecom_service_url` | 在实际微信客服账号中获取 `https://work.weixin.qq.com/kfid/...` 链接；不要从显示名称猜测 |
| 备用联系信息 | 名片的二维码、微信号与提示语 | 用于入口不可用时联系同一客服 |
| 坐席和小程序关联 | 企业微信 / 微信管理平台 | 配置接待人员、接待规则及实际小程序关联，并真机验证 |

先在微信客服管理后台创建客服账号、设置接待人员及接待时间，获取该客服对应的企业 ID 和接入链接，再在小程序管理后台完成微信客服企业绑定。在 XA“客服名片”同时填写 `wecom_corp_id`、`wecom_service_url` 并启用名片；两个字段缺一时后台会拒绝保存。根据实际订单设置负责客服，未指定时按启用名片的排序选择。配置二维码时使用可访问的 HTTPS 图片地址。正式发布前用真机从订单详情打开客服，确认落到预定坐席并能收到咨询卡片。

这些是微信客服会话配置；员工“添加企业微信好友”的名片二维码可以作为备用联系方式，不能直接当作 `wecom_service_url`。群机器人 Webhook 也不能填到客服链接里。

当前老板端调用 `openCustomerServiceChat`，客服在企业微信工作台接待，随后在 XA 手工凭证充值。**此模式不需要 XA 保存微信客服 Secret、回调 Token 或 EncodingAESKey。** 官方接口入口：[打开微信客服](https://developers.weixin.qq.com/miniprogram/dev/api/open-api/service-chat/wx.openCustomerServiceChat.html)。

## 5. 若以后要求在 XA 后台直接收发企微客服消息

这是当前原生接待方式之外的进一步开发，不是填完密钥就自动开通。需先实现回调验签解密、消息拉取与去重、游标持久化、Token 管理、坐席分配、发送状态和客户身份关联，再联合验收。

| 将来要准备 | 用途 | 当前是否有生效配置项 |
| --- | --- | --- |
| CorpID | 定位企业 | 名片已有，但 API 集成需明确统一的企业配置 |
| 被授权管理微信客服的 Secret | 换取对应接口权限的 `access_token`；以实际企微后台授权方式取得 | **未接入**；不能随意使用其他应用的 Secret |
| 回调 Token | 接收回调时校验签名 | **未接入** |
| EncodingAESKey | 解密回调消息，按平台要求生成并成对保存 | **未接入** |
| 公网 HTTPS 回调 URL | 平台向 XA 推送事件 | **未接入**；当前不能填写任意现有聊天路由充当回调 |
| `open_kfid` | 客服账号标识 | **未接入**；不是 `kfid/...` 完整访问链接，也不是群机器人 key |
| 接待成员 `userid` | 对应实际坐席、会话分配 | **未接入**；取自有权限的企业成员/客服配置 |

API `access_token`、消息同步事件中的 Token、消息游标和 `external_userid` 是运行时取得的数据，不是需要用户提前提供的永久密钥。官方入口：[微信客服开发文档](https://developer.work.weixin.qq.com/document/path/94638)。

## 6. 当前自动构建与服务器发布工作流

以 [`.github/workflows/docker-publish.yml`](../.github/workflows/docker-publish.yml) 为准，当前发布到 **Docker Hub**。

| GitHub Actions Secret 名称 | 内容 | 何时需要 |
| --- | --- | --- |
| `DOCKERHUB_USERNAME` | Docker Hub 账号/组织对应的登录用户名 | 推送镜像与服务器拉取私有镜像 |
| `DOCKERHUB_TOKEN` | Docker Hub 访问令牌，权限按镜像推送/拉取需要设置 | 自动镜像发布；服务器仅拉取时可另用受限凭证 |
| `DEPLOY_HOST` | 部署服务器地址 | 使用现有 SSH 部署阶段时 |
| `DEPLOY_USER` | 服务器部署账号 | 同上 |
| `DEPLOY_SSH_PRIVATE_KEY` | 与服务器已授权公钥配对的 SSH 私钥 | 同上；应使用独立部署密钥 |

工作流运行时的 `GITHUB_TOKEN` 由 GitHub 自动提供，不需要手动创建同名 Token。服务器拉取**私有源码仓库**还需要已配置好的只读 Deploy Key 或受限仓库凭证；当前脚本的 `git fetch` 依赖该访问能力。

另有非密钥仓库 Variable `XA_DATABASE_MODE`：`managed` 选择托管 MySQL；不设置或填 `embedded` 使用原内置数据库编排。它属于部署选项，不属于 Secret。

自动 SSH 阶段要求 `/opt/xa/.env.release`，默认使用 remote + release，GitHub 仓库 Variable `XA_DATABASE_MODE=managed` 时使用独立托管编排；只有 `.env.remote` 时明确停止。服务器先按 `RELEASE_READINESS.md` 准备正式配置、数据库账号及媒体持久卷，不能直接把旧开发环境文件改名视作已配置完成。镜像与服务器源码都固定到完整提交 SHA；Docker Hub Token 通过 SSH 标准输入交给 `docker login`，不拼入远程命令或写入项目环境文件。本轮只验证脚本，未执行镜像发布或远程部署。

若以后自动上传微信小程序：需另准备平台的小程序代码上传私钥、对应 AppID、上传机器人/CI 权限及 IP 白名单。当前仓库通过微信开发者工具手工上传，没有消费该私钥的 CI 步骤。

## 7. 按部署方案选配的凭证

| 选配服务 | 可能需要的凭证 | 当前代码边界 |
| --- | --- | --- |
| 托管 Redis | 用户名/密码、TLS 地址、端口 | 缓存可配 `DJANGO_CACHE_URL`，Celery 可配 `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND`；Channels 当前使用 `REDIS_HOST` / `REDIS_PORT`，接密码或 TLS Redis 前需同步调整 Channels 配置；当前容器 Redis 仅在内部网络使用 |
| 对象存储 / CDN | 如 COS SecretId/SecretKey 或 S3 Access Key/Secret Key，以及 Bucket/Region/Endpoint | 当前使用文件系统媒体卷；只有 `DJANGO_MEDIA_BASE_URL` 不能开启对象存储上传，需要存储适配器后再配置凭证 |
| DNS 自动化 / 自带 TLS | DNS API Token，或证书及对应私钥 | 当前 Caddy 采用常规自动 HTTPS，通常不需要 DNS Token；自带证书或 DNS 验证方案另配 |
| 备份 / 监控 | 备份目标凭证、监控 DSN / 通知凭证 | 取决于选定服务；当前未绑定云服务商，不存在通用必填 Token |

短信、邮件、微信支付均不在当前流程中。人工客服收款和后台充值不需要微信支付商户号、API v3 Key、商户私钥或支付回调 Token。

## 8. 交接顺序

1. 准备服务器、域名、数据库和服务端随机密钥，填写生产环境模板。
2. 准备真实小程序 AppID/AppSecret 和平台发布权限，完成合法域名与隐私配置。
3. 准备企微 CorpID、客服链接和实际坐席，在 XA 客服名片中配置。
4. 准备 KOOK Bot Token/频道 ID 与企微群 Webhook，按需开启派单渠道。
5. 如果使用自动发布，再在 GitHub Secrets 中配置 Docker Hub 与 SSH 凭证。
6. 在正式环境完成登录、人工充值、派单、报单、结算、提现和外部消息的实际验收；记录每一笔测试流水与发送结果。

不要把真实密钥粘贴进本文、源码、前端变量或截图。任何重置都应同步更新所有使用该凭证的 Web/Worker 环境；外部平台 Token 过期或机器人失权时，先修复配置，再按发送记录的状态处理重试。
# 玩法与转盘补充

新增玩法目录、平台 / 难度分类和转盘单不需要单独的外部密钥或 Token。奖池、权重、规则和价格在管理后台配置；正式结果由服务器生成并随订单保存。原有小程序、KOOK、企业微信等凭据要求不变。详见 [玩法配置说明](GAMEPLAY_CATALOG.md)。
