# 门店与多租户（2026-11-04）

系统按门店运行同一套业务代码，每个门店有独立名称、老板端入口、陪玩端入口、后台入口和业务数据库。原有数据保留在默认门店“声浪电竞”（标识 `shenglang`，数据库别名 `default`）；新增门店不会共享或复制原门店的客户、陪玩、钱包和订单。

## 隔离范围

| 范围 | 门店边界 |
| --- | --- |
| 老板与陪玩 | 微信身份、手机号绑定、登录会话、OTP、邀请码、个人资料与注册审核均在所属门店库内；同一人在不同门店分别建立业务身份 |
| 商品与价格 | 服务项、游戏玩法、陪玩技能/等级、老板分级、佣金、支付费率、押金/通行证、限流与超时配置独立 |
| 交易履约 | 单陪/团队下单、报名、选人、派单、转单、预约、凭证、报单、评价、退款与售后均只访问当前门店库 |
| 资金 | 钱包、充值、礼物、提现、冻结/解冻、平台留存、推荐分佣、补贴、幂等凭据与流水独立，不能跨店使用余额 |
| 运营 | 会员、积分、优惠券、抽奖、成就、生日、签到、公告、轮播、客服、站内消息、试音与权益交付独立 |
| 经营核算 | 薪资岗位/人员安排、考官业绩、夜班时间、投流渠道、每日成本、月度工资、经营 ROI 与运营字典独立；人民币与浪花口径保持原规则 |
| 权限与审计 | 门店后台角色与平台门店管理授权独立，店内管理员不能通过角色配置获得其他门店权限 |
| 异步与文件 | 缓存键、实时频道、Celery 子任务、周期任务、私有文件票据及新上传文件均保留门店上下文 |

业务查询由数据库路由选择当前门店库；`stores.db` 同时让事务、提交回调和原始 SQL 使用同一数据库。只切换 ORM 而不切换事务会破坏资金回滚，因此两者一并接入。历史数据迁移里的 ORM 操作也通过项目 `migrate` 命令绑定到目标库，避免初始化第二家店时写回原店。

实现参考了 [Django 多数据库机制](https://docs.djangoproject.com/en/6.0/topics/db/multi-db/) 与开源项目 [django-db-multitenant](https://github.com/mik3y/django-db-multitenant)、[django-tenants](https://github.com/django-tenants/django-tenants)。现系统使用 MySQL 且有大量单例配置和历史外键，因此选择每店独立业务库；未引入依赖 PostgreSQL schema 的组件，也未动态替换 Django 全局连接。

## 访问方式

三端地址通过 `?store=<门店标识>` 指定门店，路由 fragment 保留。例如：

```text
https://boss.example.com/?store=south#/home
https://provider.example.com/?store=south#/orders
https://admin.example.com/?store=south#/dashboard
```

前端请求携带 `X-Store-Slug`；文件和微信桥接页通过 URL 保留标识。未知、格式错误、尚未初始化或已停用的门店不会回退到其他店。请求头与 URL 标识冲突会拒绝。登录令牌有签名保护的 `store_slug`，不能把甲店令牌换个请求头用于乙店；跨门店必须使用该门店身份重新登录。原店历史无门店字段的令牌仅兼容原店。

微信小程序码场景有长度限制，因此扫码票据使用紧凑的门店路由提示；该提示只用于选择数据库，不授予身份权限。实际会话、短信、微信授权手机号、确认动作与签发 JWT 仍走真实后端验证。老板端只提供微信快捷登录，不引入账号密码或独立手机号注册。

## 部署配置与初始化

先备份现有数据库和媒体。迁移必须先在隔离环境验证。新增库配置由部署人员设置，不能由网页传入任意连接参数。

`XA_STORE_DATABASES` 为 JSON 对象，键是数据库别名；每项至少显式设置 `NAME`，其他连接项默认继承主数据库。示例名称仅用于说明，不是生产库：

```json
{"store_south":{"NAME":"xa_store_south"},"store_north":{"NAME":"xa_store_north"}}
```

各别名不得重复，不得使用 `default`，数据库名不得与其他门店库重合。账号可按门店设置独立 `USER`、`PASSWORD`、`HOST`、`PORT`、`OPTIONS`。凭据只放部署环境，不写入门店管理网页、源码或公开日志。

```powershell
python manage.py migrate_stores
python manage.py grant_store_platform --username <默认门店业务管理员>
python manage.py provision_store --slug demo-shop --name "示例电竞" --database store_demo --create-database --boss-url https://boss.example.com/ --provider-url https://provider.example.com/ --admin-url https://admin.example.com/ --admin-username <新门店管理员> --admin-phone <接收短信的手机号> --password-env XA_NEW_STORE_ADMIN_PASSWORD
```

先在部署进程中安全设置密码环境变量 `XA_NEW_STORE_ADMIN_PASSWORD`，命令不在参数或输出中打印密码。`--create-database` 是显式的建库动作，仅支持 MySQL/SQLite，不删除或清空已有数据库；没有建库权限时应配置适当的建库权限，不能直接接管人工预建的未标记空库。

平台管理网页可登记门店名称、入口和预配置的独立数据库别名；登记后状态为“待初始化”，不能假装已营业。执行 `provision_store` 后运行真实迁移并创建首位管理员，新店进入“待配置”，而不是开放营业；若没有可登录的后台管理员则保持待初始化。首位管理员使用统一后台及账号密码、短信或验证器完成认证后，只能访问已接入的初始化配置；老板端、陪玩端和订单、钱包等业务仍不可用。管理员已有时升级应省略 `--admin-username`，不会覆盖密码或权限。

创建数据库不会自动接管归属未知的已有库。新建流程在平台目录保留随机归属凭据，并在新库内建立匹配标记；中断后平台管理员可在门店列表继续初始化，只有双方标记、门店及目标配置一致才可继续。管理员已经创建时仅核验原身份，不覆盖账号、密码或权限；如果库已创建但标记缺失、凭据不匹配或库配置改变，停止并由运维核对，不能清库、删除或复用其他门店数据库绕过。平台目录的归属凭据须随目录数据库一起安全备份，不能仅备份业务库。完整的门店配置与开店校验仍在开发中。

首次初始化新门店须使用 `--create-database` 创建新库，或通过归属标记核验中断前已经创建的同一库；仅指定已有库别名不会自动执行迁移。已完成初始化的存量门店升级可省略该参数。为避免主机别名或代理掩盖同一数据库，新登记门店暂不接受与任何已登记门店相同的数据库名称，即使配置的主机不同；既有门店的数据与配置不受这一新登记校验改写。

新店沿用既有业务规则的配置种子，可在各自后台修改。初次初始化仅把运营配置、公告内容和薪资字典的展示字段中的原品牌换成门店名称；不会修改稳定编码、客户昵称、订单记录、工资金额或抽成比例。例如原考官岗位仍保留50元抽前、扣30%、实得35元的核算口径。后续改名、升级或重复执行初始化不会重写已维护的运营文案。

后续统一升级使用 `migrate_stores`，先升级默认库，再依次升级已初始化门店；遇到缺失数据库配置会明确停止，已停用门店不会因升级被自动启用。尚待初始化的新店会提示使用 `provision_store`，不会阻塞已有门店发布，也不会被升级命令自动开放。`--slug south` 可指定升级对象。Compose 的 Web、worker、beat 统一接收门店配置，迁移启动任务已改为该命令；不要只升级默认库就发布依赖新字段的业务代码。

例行维护使用显式门店入口，例如 `python manage.py store_command --store south verify_business_account_relations`、`python manage.py store_command --store south check_order_timeout`。该入口只接受已核对数据库路由的业务维护命令，拒绝平台授权、初始化、数据库重置、内置导入导出及任意 shell，不能通过参数覆盖数据库；裸运行历史业务维护命令仍针对默认门店。KOOK 积分长驻进程使用其专用 `run_kook_points --store south`，不能仅靠改网页地址切换后台进程。

`grant_store_platform` 是独立平台授权，需默认库内已有且可登录的业务后台账户；`--revoke` 撤销。单店的“超级管理员”角色不会自动拥有平台权限。平台管理必须在默认门店登录；承担平台入口的默认门店不能从网页停用，其余门店可由平台管理员停启。当前不提供跨店资金归集、客户合并、订单转移或用甲店账户直接管理乙店。

## 外部服务和文件

原店的 KOOK/企微环境收件配置只属于原店，新店默认不继承真实收件人。各店的外部配置使用部署环境变量 `XA_STORE_INTEGRATIONS` 的 JSON 对象（加载为服务端 `STORE_INTEGRATIONS[slug]`）；仅接受 `WECHAT_`、`SMS_`、`KOOK_`、`WECOM_`、`AUDITION_` 配置项，启停开关必须是 JSON 布尔值，不能使用字符串 `"false"`。后台自己的 KOOK 配置也存储于所属店库。微信/SMS 可共用经授权的最小桥接传输，但票据、身份和数据独立。真实扫码、微信手机号授权、短信送达、支付回调、打款及群消息需逐门店使用合法 HTTPS、域名与实际配置联调。

原店历史媒体路径保留；新店文件放在媒体根目录的 `stores/<slug>/` 下。私有 URL 需匹配门店和当前读取权限，不能通过修改文件 URL 的门店参数读取其他店。备份和恢复需同时包含中央门店目录、对应业务库与对应媒体目录。

现有历史 `deploy/tencent-*-backup.sh` 按原单库编写，不构成多门店完整备份或恢复证据；本轮没有在生产启用门店或改动生产定时备份。生产开放新门店前，运维应按实际MySQL托管/独立库配置备份全部门店库，并在隔离库验证恢复，不能只恢复默认库及中央目录。

## 验证与边界

`stores.tests_foundation` 在两个独立临时 SQLite 数据库上执行完整迁移，覆盖门店上下文清理、目录中央存储、相同用户名的店内身份、跨库实例拒绝、未知/停用/冲突入口、平台授权、待初始化门店不可开放、资金事务回滚/提交回调，以及两种 `atomic` 装饰器写法。业务、实时/异步、文件与微信票据的扩展验证分别在 `stores.tests_business`、`stores.tests_runtime` 与 `stores.tests_config`。

验证命令：

```powershell
python manage.py test stores --settings=config.multitenant_test_settings --noinput
python manage.py test stores --settings=config.multitenant_mysql_test_settings --noinput
python manage.py makemigrations --check --dry-run --settings=config.multitenant_test_settings
```

测试库、缓存、消息层、任务 broker、媒体与外部服务必须与生产隔离。接口读取通过不等于所有历史写按钮已逐一验收；最终执行数量、实际业务场景与未覆盖的外部链路以本轮验收记录为准。生产库迁移、新店生产数据库和真实外部服务开通不由代码测试替代。

首次实施的历史结果保留在[三端验收记录](../../fronted/ACCEPTANCE.md)：当时107项双店MySQL专项、25项共享前端测试及三端构建通过。后续失败修复、当前React历史测试迁移、双店真实浏览器和完整回归，以[多门店与三端续验记录](./multitenancy-validation-20261104.md)及[逐页证据矩阵](../../fronted/verification-matrix.md#最新执行结果与证据层级)为准，不再沿用首次实施的总数或未完成状态。

MySQL测试设置沿用本机既有隔离验收凭据与专属`xa_test_`数据库，其他环境应先准备等价的隔离MySQL配置。未发布生产；真实外部微信/SMS/支付/群消息仍须逐店联调，不能由本地替身或逐页浏览器读取替代。
