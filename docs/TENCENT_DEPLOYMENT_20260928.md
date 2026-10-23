# 兴安电竞腾讯云部署验收 · 2026-09-28

## 已部署入口

| 用途 | 地址 | 状态 |
| --- | --- | --- |
| 管理后台 | https://admin.xaesports.club | HTTPS，管理员登录与页面核验通过 |
| 陪玩端 | https://provider.xaesports.club | HTTPS，同源 API 与 WebSocket，登录页核验通过 |
| 老板小程序 API | https://api.xaesports.club/api | HTTPS，真实微信登录通过 |
| 老板小程序实时连接 | wss://api.xaesports.club/ws/orders/ | 认证与心跳通过 |
| 主域名和 www | https://xaesports.club | 跳转陪玩端 |

DNSPod 的根域、www、api、admin、provider 均指向用户指定腾讯云主机 `43.167.213.74`。五个域名已取得 Let's Encrypt 证书，Caddy 管理续期。公开业务端口为 80/443，数据库、Redis、后端和原来的 8080/8081 页面端口不再映射公网。

服务器为现有 Ubuntu 24.04、2 核、约 2 GB 内存实例。此部署复用原数据库和媒体卷，不代表已经达到 160 人同时在线的持续容量目标，也没有另购云数据库或 COS。

## 发布内容和配置

部署目录 `/opt/xa-upgrade-20260928`，当前入口 `/opt/xa-current`。当前源码来自本工作区，基线 Git HEAD 为 `194463853eb75b9fd6b6297bb018fedad325bcef`；包含尚未提交的业务改动，以 `manifest.json` 文件摘要和实际镜像为发布依据，不把 Git HEAD 单独视为完整源码版本。

初次部署的后端镜像为 `sha256:4285facc7f57baa879010be627ee050b4f3ee3bbc7f93dadaef6c7f761397f53`；KOOK 升级镜像为 `sha256:43efce6cb45bcba9beee2cfc804652dcab0fa4a159da92808d6fb15d4ed67f62`。后续微信客服升级的实际镜像以 `/etc/xa/release.env`、运行容器与对应验收记录为准。MySQL/Redis 复用原镜像，Nginx/Caddy 固定拉取摘要；完整部署声明见 `docker-compose.tencent.yml` 与 `deploy/compose.tencent-domains.yml`。

运行两个 Daphne Web 副本、一个 Celery Worker、唯一一个 Beat、MySQL、Redis、两个静态前端和 Caddy。已设置容器内存上限和日志轮转。后端关闭 DEBUG、模拟微信登录、任意 CORS；应用使用仅授权 `xa_db` 的专用数据库账户。充值凭证、报单图片和资金请求唯一标识均强制检查。私有财务、报单和会话图片不能通过公开媒体路径绕过权限读取。

本轮补充健康端点 `/api/health/`，实测数据库和 Redis 依赖，异常返回 503 并隐藏连接信息；修正后台金额转换、样式检查与正式小程序 HTTPS 配置。一起部署的业务版本含充值防重、资金请求恢复、审核后结算、转单分账、提现账户快照、实时权限及登录会话隔离；全量回归验证这些行为。

配置 `/etc/xa/release.env` 与管理员资料 `/etc/xa/admin-access.json` 均为 root 600。微信 AppSecret 只在后端运行环境和受限备份中保存，不进入小程序、静态网页或源码镜像。原管理员密码和角色保持原状态；新建运营管理员 `xa_deploy_admin`。本机交付资料位于 `.tools/tencent-deploy/admin-access.json`，仅桌面用户可读，未写入 Git。

用户确认暂不导入本地价目、图片和运营内容，由后台手工维护。目前有 8 个默认玩法、0 个服务商品；小程序显示待开放状态。正式售卖前应先配置游戏类目、服务分类、陪玩账户及档位、商品价格与规则、客服名片。不要把本地测试库的账号、钱包或示例价格当成生产数据。

初次部署时 KOOK、企微群投递关闭。后续 KOOK 接入现已启用，后台数据库配置优先于旧环境开关；企微仍关闭。当前充值与提现沿用客服核实收款和人工打款业务，不接微信支付商户 API。老板浏览器试音入口没有单独部署；启用分享功能前需设置实际入口，不能使用默认示例链接。

## 微信交付

AppID `wx495c9fb56eb3ad24`。原生 47 个注册页面、48 个 WXML 和 49 个 WXSS 通过官方编译。trial/release 指向生产 HTTPS/WSS，项目开启合法域名检查。

用户确认已在微信公众平台配置 request/upload/download 域名 `https://api.xaesports.club`、socket 域名 `wss://api.xaesports.club` 和服务器 IP 白名单。开发者工具真实登录 code 经云端兑换成功；仅检查身份，不创建测试老板或给钱包入账。首页和目录实际读取云端 API。

官方 CLI 已成功上传开发版本 `2026.9.28`，包体 653,057 字节（637.8 KB），生成手机预览二维码。用户进一步确认手机预览与手机号授权正常；这是用户真机验收反馈，自动化报告仍保留自身未执行真机/手机号交互的原始标记。

用户明确选择先保留开发/体验版，本次不进入正式提审。微信开发版本上传不等于正式发布；以后指定体验版本、隐私与类目资料核对、提审及审核通过后的发布需在持有小程序的微信公众平台账号完成。

## 验收证据

| 检查 | 结果 |
| --- | --- |
| 云端实际镜像 + 隔离 MySQL 全量回归 | 1,069 项，1,057 通过，12 按环境跳过，零失败；154.949 秒 |
| 本机隔离 MySQL 全量回归 | 1,069 项，12 跳过，零失败 |
| 客户端组合回归 | 133 通过，零失败 |
| 三端类型检查、两端 H5 构建、原生官方编译 | 通过 |
| 原库备份及隔离恢复 | 58 张表、413 行、0 媒体文件摘要一致 |
| 新镜像在恢复库执行迁移 | 通过；原余额、冻结额、流水、提现与订单事实摘要一致 |
| 生产迁移及原资金事实核对 | 通过，后台定时任务在核对后启动 |
| MySQL 隔离级别/存储引擎/时区 | READ COMMITTED / InnoDB / 命名时区通过 |
| 后台真实登录与 49 个只读模块 | 全部通过，77 项权限，匿名访问拒绝、令牌刷新通过 |
| 三域健康、私有媒体、CORS | 通过 |
| 公网 WSS | 认证与 ping/pong 通过 |
| Redis → Worker → 结果后端 | Celery 内置无业务副作用任务往返通过 |
| 一个 Web 副本停机接管 | 18.185 秒、41 次公网健康请求、零失败、p95 369 ms；副本已恢复健康 |
| 每日备份首次执行 | 通过，校验和一致 |

停机接管探测只验证短时可用性，不替代资金写入负载和 160 人持续容量测试。真实运营的收款、打款和外部消息投递未被测试脚本执行。

云端全量日志 `/opt/xa-current/cloud-regression.log`；生产财务摘要 `finances-before-live.json` / `finances-after-live.json`。本机报告和截图在 `.tools/tencent-deploy/`，包括 `cloud-web-report.json`、`cloud-failover.json`、`native-cloud-report.json`、微信上传/预览结果与后台截图。避免公开分享包含登录资料的整个目录。

## 运维与恢复

日常命令从 `/opt/xa-current` 执行：

```sh
sudo docker compose --env-file /etc/xa/release.env -f docker-compose.tencent.yml -f deploy/compose.tencent-domains.yml ps
sudo docker compose --env-file /etc/xa/release.env -f docker-compose.tencent.yml -f deploy/compose.tencent-domains.yml exec -T --index 1 web python manage.py check_mysql_readiness
sudo systemctl status xa-backup.timer xa-backup.service
sudo journalctl -u xa-backup.service --no-pager -n 30
curl --fail https://api.xaesports.club/api/health/
```

每日中国时间约 03:30 执行 `xa-backup.timer`，使用数据库事务快照和随后获取的媒体文件集合，包含受限配置、部署清单和校验和；不自动删除备份。首次目录 `/var/backups/xa/daily/run-20260928T090728Z-VeOUoh`。定期检查空间和备份作业退出状态；当前没有异地副本、外部告警收件人或自动清理策略，不能承诺整机故障后的恢复能力。

升级前可恢复备份 `/var/backups/xa/pre-upgrade-20260928T082754Z-ZyOTIl`，另有停写后捕获的 `database-before-activation.sql`。迁移演练恢复库 `xa_restore_20260928082828_25279204` 保留供复核。

故障时先检查健康、容器重启/OOM、数据库和磁盘状态。恢复应停写后新建隔离数据库和媒体目录，校验恢复结果再切换；先保存当前数据。数据库迁移后不要直接运行旧后端覆盖新库，不使用 `docker compose down -v`。单个 Web 的失败可重启对应副本，无需还原整库。

## 尚需运营验收

正式商品、档位、客服收款入口由用户手工配置；已获用户确认，不自动导入本地演示数据。小程序按用户明确选择保留开发/体验版。以后正式提审发布、企微等其他渠道、异地备份及目标人数持续容量测试应单独完成并记录实际证据。

## 小程序媒体资源修复

后续资源预算修复已上传开发版本 `2026.9.28.1`：包内图片与音频合计由 295,283 字节降至 29,607 字节，整个上传代码包由 653,057 字节降至 387,796 字节。两张展示插画通过既有 HTTPS 静态资源域名原样加载，四个相关页面在微信模拟器中显示正常；客户端回归增至 136 项通过。详情与部署位置见 [小程序媒体预算修复记录](MINIAPP_MEDIA_BUDGET_20260928.md)。

## KOOK 后台配置升级

新增“订单管理 → KOOK 派单配置”，支持 Bot Token 加密存储、多频道按顺序匹配、每频道文案、角色提醒、预览与显式测试。新单和拒单回池自动投递，也可通过“一键发单”手工发送；配置直接从数据库读取，保存后不用重启。

用户指定服务器 `1913355034430746`、文字频道 `6484022693927157`。真实后台预览和保存通过；已向该频道发送用户授权的一张标明非真实订单的 `TEST-KOOK-001` 测试卡片，KOOK 消息 ID 为 `0900c801-1bca-4de9-be10-afce48262fbb`。自动新单和拒单回池已启用，初始规则接收全部匹配订单，不提醒身份组。

实际新镜像的独立 MySQL 全量回归 1087 项，12 跳过、零失败，156.217 秒。生产仅执行两个新增迁移；两个 Web 副本健康，Web/Worker 配置一致，任务队列往返通过，原订单与资金事实摘要不变。发布前备份 `/var/backups/xa/daily/run-20260928T104050Z-1xXwev`；媒体修复版本快照保存在 `release-history/2026.9.28.1`。隔离构建与回归记录位于 `/opt/xa-kook-build-20260928`。

新单/拒单真实业务链由隔离测试验证，生产联调未创建业务订单或资金流水。操作与状态排查见 [KOOK 派单操作](KOOK_ADMIN_SETUP.md)。

## 小程序企业微信客服

已在“运营管理 → 客服名片”保存默认“兴安电竞客服”，企业 ID `ww9c709c084040a1af`，接入链接 `https://work.weixin.qq.com/kfid/kfc6445fdf249e1cefd`。用户确认小程序平台绑定与接待人员分配完成。客服配置通过现有 API 下发，无企业 Secret 或消息回调密钥进入客户端。

新版原生客服页补充咨询编号复制、充值和订单咨询上下文、旧微信/打开失败提示及账号切换保护。开发预览保留微信失败回调和导航提示，方便定位真机问题。企业微信会话由企业微信工作台接待，不自动同步为本站聊天记录或修改钱包与订单。此轮开发包版本为 `2026.9.28.3`，上传及手机接待验收结果以 `.tools/tencent-deploy/wecom-*` 记录为准；不进行正式提审或发布。操作说明见 [微信客服接入](WECOM_CUSTOMER_SERVICE_SETUP.md)。
