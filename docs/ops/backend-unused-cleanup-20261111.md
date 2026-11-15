# 后端无引用代码清理（2026-11-11）

本轮以清理开始时的工作树为基线，保留已有未提交修改。只删除已确认没有调用引用的定义，不修改接口行为、数据库字段、结构迁移或本机业务数据。

## 引用核对与删除范围

扫描后端、当前三端、测试、脚本、部署配置及共享包，先用 AST 和标识符检索筛选，再逐项检查调用、字符串导入、Django 自动发现和配置引用。Django AppConfig、信号接收器、模型导出等不能按文本引用次数判断为死代码，均保留。

| 文件 | 删除项 | 核对结果 |
| --- | --- | --- |
| `services/backend/club_accounts/permissions.py` | `HasAccountType` | 无使用方；当前权限继续使用已有认证和角色权限实现 |
| `services/backend/console/auth.py` | `_complete_login` | 旧 OTP 登录完成函数无调用；当前后台登录经 `ConsolePasswordLoginView` 执行，保留原路由及当前验证流程 |
| `services/backend/console/views.py` | `_resolve_provider_report_commission` | 旧报单抽成辅助无调用；当前审核和财务展示使用现有领域服务及财务快照 |
| `services/backend/console/views.py` | `_resolve_report_order_share` | 旧报单分账辅助无调用；历史文档有描述，当前业务代码和测试不调用它 |
| `services/backend/orders/services.py` | `release_rejected_provider` | 已无调用的拒单释放份额函数；不改当前拒单限制和客服协调流程 |
| `services/backend/wallet/models.py` | `get_pass_delays` | 全档位延迟汇总辅助无调用；实际使用的 `get_pass_delay_seconds` 保留，模型类保持不变 |

同时移除 `console/auth.py` 中已无用途的 `ClubAccount`、`authenticate_account`、`issue_account_tokens` 及模块级 `store_context` 导入，以及一段不再有对应实现的旧报单说明。函数内部仍使用的门店上下文导入保留。

`.tools` 中检索到的同名实现或导入位于历史 `before/` 备份目录，不是当前运行入口，未修改这些备份。清理前五个文件的完整工作树版本及本轮独立补丁保存在忽略提交的 `.tools/backend-unused-cleanup-20261111/`，没有用 Git HEAD 覆盖他人修改。

## 经检查仍需保留

- `/api/admin/auth/login`：当前后台及门店中立登录流程仍调用；保留其委托的现行认证流程。
- `/api/users/account-login/`：当前陪玩端认证页面及后端回归仍有调用。
- 订单 `start-service`、`start`、`tip`、`reject` 等路径：现有页面、动态操作或后端测试仍有引用，不仅凭路径命名认定为旧接口。本轮没有删除路由。
- `LegacyAccountMap`、`request.legacy_user` 和用户外键关联：当前初始化、认证、订单、钱包、审计和测试仍依赖，不能当成独立历史模块直接删除。
- `LEGACY_FORCE_AUTH_COMPAT` 测试适配：现有回归仍依赖它，生产默认关闭；本轮不重写测试基础设施。
- 退款、分段结算、财务快照中的历史分支与失败保护：仍在调用链中，未以“legacy”命名作为删除依据。

保留这些引用不增加旧版本迁移或上线接管要求；这里只说明当前实现的实际依赖。

## 验证与数据边界

以清理开始时的源文件为基线做 AST 对比：除上述六个定义及列明的登录导入外，其余可执行 AST 不变。396 个迁移文件的 SHA-256 相同，48 个模型模块中全部类定义的 AST 相同。

- 后端系统检查通过；`makemigrations --check --dry-run --settings=config.migration_test_settings` 返回 `No changes detected`。
- 认证、OTP、后台微信登录、初始化、订单结算、陪玩状态和通行证相关 51 项测试，清理前后均为 49 通过、1 失败、1 跳过。既有失败为 `console.test_bootstrap.BootstrapConsoleTest.test_created_business_admin_can_login_and_existing_user_is_preserved`，只提交账号密码后期望 `code=0`，当前接口返回 `400`；本轮不更改认证或测试预期。跳过项为需要 MySQL 的并发测试。
- 另外 17 项报单审核、双陪分账、财务快照测试全部通过。
- 两店独立数据库下的中立后台入口、登录凭据归属、令牌、媒体及 WebSocket 隔离测试 14 项全部通过。
- 合计 82 项测试：80 通过、1 个基线已有失败、1 个因 SQLite 不支持行锁而跳过的并发项，没有新增失败。
- 测试使用配置明确指定的 SQLite 内存数据库，不连接既有 MySQL 开发库；双门店测试使用 `config.multitenant_test_settings` 的两套一次性内存库并执行迁移。没有清空或重建本机已有业务库，也没有调用真实资金渠道。
- 通过 `deploy/dev.ps1 RestartBackend` 重启本机受控后端，再执行 `deploy/dev.ps1 Verify`：后端、两店数据库映射及 Redis 健康，五个入口均返回 HTTP 200。外部传输仍使用已有隔离替身。

系统检查和 SQLite 测试不代表已覆盖 MySQL 并发或外部微信、短信、支付渠道。生产部署及数据清理不在本轮执行范围内。
