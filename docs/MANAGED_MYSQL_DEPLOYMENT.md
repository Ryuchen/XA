# 托管 MySQL 部署

更新：2026-09-23。增加外部 MySQL 的独立生产编排，复用现有应用镜像、迁移、业务配置与网关。尚未购买腾讯云资源或执行公网部署。

## 配置与启动

托管方式使用仓库根目录 `docker-compose.managed.yml`，**单独使用此文件**，不叠加原有 remote/release 编排。没有内置数据库服务或 `mysql_data` 卷，也不需要云数据库 root 密码。Redis、媒体和静态资源仍按原部署方案运行；Web 为两个实例，Beat 为一个。

1. 在与 CVM 同地域、同 VPC 的数据库实例创建应用库和专用非 root 账号。授予该库所需的建表、迁移和业务读写权限；账号限制到应用来源，启用实例 TLS，并要求该账号使用 TLS。
2. 从数据库平台取得 CA 公钥证书，保存到服务器受控目录，例如 `/etc/xa/mysql-ca.pem`。确认数据库访问地址能从 CVM 解析和连通，并与服务器证书中的身份匹配；应用会验证证书链与主机身份，不关闭校验来规避不匹配。
3. 将 [托管配置模板](../deploy/managed.env.example) 复制为服务器仓库根目录 `.env.release`，填写数据库、三个域名及真实小程序配置。CA 文件通过 Compose secret 只读挂载到四个后端进程。

| 配置 | 内容 |
| --- | --- |
| `MYSQL_HOST` / `MYSQL_PORT` | 可从应用所在私网连接的数据库地址、端口；不是 `db` 或应用服务器的回环地址 |
| `MYSQL_DATABASE` | 已创建的应用数据库 |
| `MYSQL_USER` / `MYSQL_PASSWORD` | 仅用于该业务库的非 root 账号与密码 |
| `MYSQL_SSL_CA_FILE` | 应用服务器上的 CA 公钥证书文件绝对路径；不填私钥 |
| `MYSQL_CONNECT_TIMEOUT` | 建立数据库连接的期限，默认 10 秒，可配置 1–300 秒；不改变业务 SQL 或事务超时 |

手工部署时，先确定不可变镜像版本（`sha-完整提交SHA`）和 Docker Hub 命名空间，使用与镜像对应的源码，再执行：

```sh
docker compose --env-file .env.release -f docker-compose.managed.yml config --quiet
docker compose --env-file .env.release -f docker-compose.managed.yml pull
docker compose --env-file .env.release -f docker-compose.managed.yml up -d --no-build --wait --wait-timeout 180
docker compose --env-file .env.release -f docker-compose.managed.yml exec -T web python manage.py check_mysql_readiness
```

脚本化部署使用已有 `deploy/deploy-release.sh`，执行环境额外设置 `XA_DATABASE_MODE=managed`，其余 `DEPLOY_COMMIT`、`XA_IMAGE_TAG`、`DOCKERHUB_NAMESPACE` 规则不变。GitHub 自动部署在仓库 **Variables** 中设置 `XA_DATABASE_MODE=managed`；未设置时继续选择原来的内置数据库模式。这个选择由部署脚本/工作流环境控制，仅在 `.env.release` 添加同名变量不会切换脚本使用的 Compose 文件。

## 启动顺序和连接要求

迁移服务先校验实际连接的 TLS、READ COMMITTED、InnoDB 和命名时区转换，再迁移、复查、收集静态资源。Web、Worker 和 Beat 都等待迁移成功；不会在迁移失败后继续启动新实例。

`config.database.mysql_database` 组装数据库配置。托管模式拒绝空地址、root 账号、空/开发默认密码和缺失或无效 CA。TLS 后端沿用 Django MySQL 事务与类型实现，仅替换连接建立；PyMySQL 验证证书链与服务器身份。若服务器不声明 TLS 支持，在发送认证数据前拒绝连接，避免驱动降级为非加密连接。

应用账号依然需要平台侧的 TLS 限制和来源授权。首发方案中的托管数据库双节点、私网访问控制和云端自动备份须在实际实例配置中确认，Compose 不会替代这些设置。

实现依据：[PyMySQL 连接参数](https://pymysql.readthedocs.io/en/latest/modules/connections.html)、[Django MySQL 配置](https://docs.djangoproject.com/en/6.0/ref/databases/#mysql-notes)。

## 从已有数据库切换

新增配置不会复制旧库。已有运营数据迁移时，先建立停写窗口，备份并恢复目标库、核对余额/冻结额/订单/流水及媒体，验证新连接后再切换。保留旧库与备份；不要直接把部署模式改为 managed，让应用连接一个空库后继续运营。

已有 MySQL 数据卷内的账号密码不会因修改环境变量自动改变。首次开通使用数据库平台设置的真实账号；本地独立验证账号不能复用到云端。

## 验证方法与边界

- `python3 scripts/test-managed-compose.py` 读取实际 Docker Compose 模型，核对无内置数据库、无后端公网端口、统一 TLS 环境和迁移依赖，并与原生产配置比较业务开关。
- `node --test scripts/test-deploy-release.mjs` 验证两种部署模式及错误模式的拦截，不连接真实部署服务器。
- 后端 `config.test_database` 验证有效/无效配置、实际 PyMySQL TLS 上下文、认证前拒绝 TLS 降级。

目标腾讯云实例及内核版本尚未提供。本地 MySQL 验证不能代替目标 TXSQL 上的迁移、并发、证书、私网、备份恢复和持续负载验收。资源选择及外部平台条件见 [腾讯云方案](TENCENT_DEPLOYMENT_PLAN.md)。

## 2026-09-23 本地验收记录

配置检查 4 项、部署脚本 7 项通过；后端数据库配置与原启动检查合计 27 项通过。

另建隔离的 `xa-managed-db-test` 数据库项目与 `xa-managed-validation` 应用项目，使用本地生成的短期 CA/服务器证书、专用非 root 账号和 `REQUIRE SSL`。数据库不属于应用的 Compose 服务，两个项目通过专用 Docker 网络连接。

| 实际检查 | 结果 |
| --- | --- |
| 使用正确 CA 和服务器身份 | TLS 会话成功，实际会话加密套件非空 |
| 使用同一服务器的错误主机名别名 | 实际连接因证书身份校验失败而拒绝 |
| 使用不相关 CA | 实际连接因证书链校验失败而拒绝 |
| 生产迁移与数据库预检 | 完整迁移完成；READ COMMITTED、InnoDB、命名时区转换通过 |
| 访问游玩项目 HTTP API | 经 TLS MySQL 查询成功 |
| TLS 后端关键回归 | 56 项通过，覆盖发布业务流、并发派单、报单结算、提现账户与申请、资金幂等回执、微信绑定并发及新配置检查 |
| 完整应用部署 | 8 个应用容器运行，含两个 Web、一个 Beat，无内置数据库；Web、Worker、Beat 实际数据库预检通过 |
| Redis 与异步任务 | 真实队列 → Worker 执行超时扫描 → 结果查询通过 |
| 三端入口与收尾 | 临时 API、管理端、陪玩端入口均返回 200；验收容器已停止，数据卷保留，原有 18080/18081/18082 入口继续正常 |

56 项使用单独的 `test_xa_test_managed` 测试库、内存通知/任务和临时上传目录，没有使用现有本地业务库。它是 TLS 部署专项，不与历史 955 项全量回归相加计算覆盖率，也不宣称已执行最新全量回归。

首次启动时，验证夹具的 `mysqladmin ping` 过早把 MySQL 初始化临时进程视为就绪；夹具改为经 TCP、使用真实账号执行 `SELECT 1` 后继续通过，沿用同一数据卷和配置，未重置数据库。

证据：`.tools/wsl-setup/managed-compose-tests.log`、`managed-deploy-script-tests.log`、`managed-mysql-drill.log`、`managed-mysql-stack.log`。临时数据库配置与证书保存在 WSL `/var/lib/xa-local-validation/managed-mysql-*` 的受限目录；短期证书仅用于本地验证，不能移用到云端。
