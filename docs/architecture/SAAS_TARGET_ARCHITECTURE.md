# 企业级 SaaS 目标架构与重构设计

版本：2026-11-17；状态：**目标架构设计（规划），不是已实现或已验收的声明**。现状描述以 2026-11-17 源码盘点为据；目标设计落地前不得称为现有能力。

配套：[SaaS 平台产品设计](../prd/35-企业级SaaS平台产品设计.md)、[重构实施计划](REFACTORING_PLAN.md)、[企业级总体规划](../development/26-企业级总体规划.md)。早期资料 [ARCHITECTURE.md](ARCHITECTURE.md)、[DATABASE_DESIGN.md](DATABASE_DESIGN.md) 部分内容早于首版决策，冲突时以本文与 PRD/development 较新约定为准。

## 1. 设计目标与硬约束

**目标**：把现有模块化单体演进为职责清晰、租户隔离可证明、权益集中裁决、事件驱动、可观测的企业级 SaaS 平台，支撑多门店规模化开通与运营。

**硬约束**（沿用已确认决策，[dev/01](../development/01-架构权限与多租户.md)、[dev/12](../development/12-研发评审与待核实事项.md)）：

1. 保留 Django 模块化单体与一店一独立业务库＋平台控制库；不引入 django-tenants/db-multitenant，不拆微服务，不换数据库体系。
2. 不跨库事务/外键；店库事务＋稳定锁序（先订单后钱包、多陪玩按账号 ID 排序）＋同事务 outbox＋commit 后唤醒与定时补偿。
3. 金额整数内部单位（10 单位=1 店币，人民币 1 元=10 店币，薪资人民币分），禁浮点；后缀协议 `*_raw/_fen/_coin/_point_centi`；旧字段不静默改义。
4. 幂等 `client_request_id` 同键异容 409；状态变更经 state_machine；钱包变更同事务流水＋行锁；付款以服务端锁内重算为准。
5. License 核心订单资金不可配置关闭；授权码不落明文日志；前端不复刻计价，WS 不代替回读。
6. 保留 `fronted/` 现有三端界面与接口契约兼容；前端由其他开发负责，契约变更须同步。
7. 首版不做旧数据兼容迁移；空库全新部署为上线基准。

## 2. 现状架构盘点（2026-11-17 源码证据）

| 维度 | 现状 | 证据 |
| --- | --- | --- |
| 应用组织 | 17 个 Django app 平铺；orders 489 文件（ordinary_*/points_*/grab_* 前缀微文件）、console/views.py 6158 行、orders/views.py 2536 行 | `config/settings.py` INSTALLED_APPS |
| 租户路由 | `stores/router.py` StoreRouter＋`ISOLATED_CONTROL_PLANE`（15 个租户 app 访问 default 抛错）；`stores/context.py` ContextVar，**无上下文回落 default**；`stores/db.py` 租户感知事务/on_commit | stores app |
| 上下文注入 | HTTP `stores/middleware.py` 按 Host/X-Store-Slug/query 解析；WS `orders/consumers.py` resolve_store＋`stores/channels.py` 组名 store 哈希；Celery `stores/tasks.py` StoreTask headers 注入＋约 60 个 PERIODIC_STORE_TASKS 逐店扇出 | 各文件 |
| 配置 | 单一 settings.py＋约 30 个 *_test_settings 变体；**133 处 env_bool 开关**（含 30+ 个 *_NOTICE_RECOVERY_ENABLED） | settings.py:216-589 |
| 任务 | 7 个 tasks.py 分散；beat **61 条**周期任务，大量 60s 通知补偿扫描 | settings.py:449 起 |
| API | `/api/<app>/` 无版本化；DRF fail-closed 默认权限；权限注册表 `console/permissions.py`（模块:动作） | config/urls.py |
| 实时 | 仅 OrderConsumer（ws/orders/），组名 `account_{id}`；token 鉴权 `club_accounts/channels_middleware.py` | orders/consumers.py |
| 耦合 | orders↔wallet 双向 import；orders/views 依赖 console.permissions 与 payroll_event_barrier；wallet/deposit_applications 调 orders 派单锁；资金规则分散 4 处（orders/refund_math、orders/points_redemption_math、console/payroll_calculation、wallet/refund_priority） | 见 §4 |
| 测试 | 406 个 test_*.py 平铺 app 内，_mysql/_browser/_multistore 后缀分层；common/mysql_test_support 支撑 | 各 app |

**结论**：多租户基础设施（路由、上下文、任务扇出、通道隔离、控制面隔离开关）已达企业级骨架水准；主要差距在**领域边界与依赖方向、权益裁决分散、开关治理缺失、补偿任务碎片化、API 治理与可观测性**。重构以治理这些差距为主，不推倒重来。

## 3. 目标拓扑：控制面 / 数据面

```
┌────────────────────────── 控制面（default 库） ──────────────────────────┐
│ stores：租户目录/域名路由快照/建店状态机/平台身份/授权审计               │
│ license：授权码/合同/收款/宽限/提醒队列/配额计数                        │
│ entitlements：档位权益目录/增值配置/签名授权快照（新增模块，见 §5）      │
│ platform-ops：跨店只读聚合/用量日快照/平台总账                          │
└──────────────────────────────────────────────────────────────────────────┘
        │ 签名快照缓存（降级可用）            │ 逐店只读采集（Celery 扇出）
┌───────▼────────── 数据面（tenant_<slug> 每店一库） ──────────────────────┐
│ identity：club_accounts/users（账号/OTP/微信绑定/会话）                  │
│ commerce：orders（派单/抢单/预约/履约/售后/加购/房间）                   │
│ money：wallet（钱包/押金/提现/流水）＋金额内核（见 §4.3）                │
│ growth：engagement/coupons/签到抽奖成就（会员权益）                      │
│ communication：chat/site_messages/咨询（客服池/通知）                    │
│ workforce：console 薪资经营域（员工/工资/ROI）                           │
│ console-api：后台聚合视图层（只编排，不持有业务规则）                    │
└──────────────────────────────────────────────────────────────────────────┘
```

- 控制面故障：门店按最近成功校验的签名快照继续营业，到期/宽限按缓存时间推进，恢复后重验（已确认规则；快照路由已实现，**业务拦截与离线授权未完成**）。
- `ISOLATED_CONTROL_PLANE` 目标为生产常开；当前仅 SQLite/模拟验证，须先完成隔离 MySQL 全流程与物理断库演练（M5）。
- 建店按七态状态机分步持久化（dev/01 §4），替换"迁移完即 ACTIVE"。

## 4. 领域边界与依赖治理

### 4.1 依赖规则（目标）

```
console-api（视图编排） ──▶ 各领域服务层 ──▶ 领域模型/金额内核
各领域之间：只允许 单向服务接口 或 同事务 outbox 事件，禁止双向 import
任何业务模块不得 import console（后台是呈现层，不是共享库）
共享基础：common（锁/幂等/状态机/outbox）、stores.context/db（租户）、金额内核
```

### 4.2 现状违规与解法

| 违规 | 现状证据 | 目标解法 |
| --- | --- | --- |
| orders↔wallet 双向 import | `orders/services.py:35-37` 引 wallet.services/notifier；`wallet/income.py:213-389` 反向引 orders.models/financial_lock | 资金动作收敛为 wallet 对外的**资金服务接口**（付款/退款/冻结/结算，入参为订单侧值对象）；orders→wallet 单向调用；wallet 需要的订单事实由调用方传参或订阅 outbox 事件，不再反向 import orders.models |
| 业务层依赖后台 | `orders/views.py:20,778` 引 console.permissions、payroll_event_barrier | 权限注册表迁至 `common/permissions`（或独立 access app），console 与 orders 共同依赖底层注册表；工资事件屏障接口下沉到 workforce 服务层，orders 经事件/接口触发 |
| 押金调派单锁 | `wallet/deposit_applications.py:68` 调 orders.services 派单锁 | 锁序协议归 common/financial_lock 统一持有；押金补差与派单竞争按既定锁序（先订单后钱包）经服务接口协调 |
| 遗留迁移函数被权限层引用 | `console/permissions.py:10` import club_accounts.services 迁移函数 | 随权限注册表迁移一并清理，确认无调用后删除 |

### 4.3 金额内核（统一资金规则）

资金规则现分散 4 处：`orders/refund_math.py`、`orders/points_redemption_math.py`、`console/payroll_calculation.py`、`wallet/refund_priority.py`。目标：

- 新建后端金额内核模块（建议 `common/money_kernel/`，与前端 `packages/money` 口径对齐但各自实现，后端为唯一计价事实源）。
- 收纳：退款构成拆分（本金/赠送/赔付顺序与取整）、积分抵扣与冲正、分成基数、工资绩效口径的**纯函数**部分；各处改为调用内核，禁止复制公式。
- 迁移方式：先以 characterization tests 固化现有数值行为（含 T07/T08 等边界），再逐处替换实现，数值结果必须逐分一致；口径冲突以 PRD 29/30 与用户最新决定为准，不得借重构改口径。

### 4.4 上帝模块拆分

- **orders**：489 个前缀微文件形式化为子包 `orders/ordinary/`、`orders/points/`、`orders/grab/`、`orders/reservation/`、`orders/negotiation/`、`orders/addon/`、`orders/room/`，各子包内 models 不动（Django app_label 不变，**零迁移**），services/views/tests 随包归位；对外 URL 与导入路径经 `orders/__init__` 兼容导出过渡，前端契约不变。
- **console**：views.py 6158 行按域拆为 `console/views/`（orders_admin、wallet_admin、payroll、engagement_admin、store_config、platform_ops…），URLconf 不变；薪资计算纯函数迁金额内核后，console 只留编排与权限。
- **stores**：80 个 license_*/platform_* 文件归入 `stores/licensing/`、`stores/platform/`、`stores/tenancy/` 子包；entitlements 裁决独立成模块（§5）。

## 5. 权益裁决与开关治理（SaaS 核心）

### 5.1 三层开关分类治理

133 处 env_bool 开关按性质分类，目标收敛为：

| 类 | 例 | 目标归属 |
| --- | --- | --- |
| 部署/基础设施 | XA_DATABASE_MODE、XA_ISOLATED_CONTROL_PLANE、DYNAMIC_STORE_DATABASE_ENABLED | 保留 env，登记到部署清单（deploy README） |
| 平台增值/档位权益 | VIP_SAFE_BOX_*、ORDER_TRAFFIC_ENABLED 等功能级 | 迁入平台增值功能配置（控制库，仅平台超管可改、留审计、保存即生效），env 仅作部署默认值 |
| 门店业务配置 | 积分开关、通知开关、延迟配置 | 门店系统字典/配置目录（PRD 12/18），已有事实源不另造巨型字典 |
| 通知恢复补偿 | 30+ 个 *_NOTICE_RECOVERY_ENABLED | 合并为统一通知恢复框架的单一开关＋按事件类型注册（§6.2），逐个验证后退役 |

每个开关建立登记：名称、类别、事实源、默认值、启用证据、退役条件。迁移期间 env 与配置目录双读时以配置目录优先，行为变化须有测试证据。

### 5.2 统一 entitlement 服务

- 新增 `stores/entitlements.py`（或子包）：输入 `(store, feature, actor_role, action)`，输出 允许/拒绝＋原因码；裁决顺序＝角色权限→License 状态/配额→平台增值配置→门店开关（产品设计 §3.3）。
- 数据源：控制库 License/增值配置＋**签名授权快照**（已有快照机制扩展 feature 维度）；控制库不可用读快照，无有效快照时新增账号/增值发放 503 暂停、核心业务保留（已确认）。
- 接入点：全部新增账号入口、增值发放入口（券/积分/安全箱/成就/礼物权益等）、可购模块入口（BOSS_H5）；以统一装饰器/服务调用接入，CI 增加静态检查禁止绕过（新增发放入口必须声明 entitlement 键）。
- 配额计数沿用现有并发安全实现（License账号名额并发计数专项）；陪玩显式注销释放名额随 M5 接入。

## 6. 事件、任务与实时

### 6.1 outbox 统一化

- 已确认方案：事件同事务写 outbox、至少一次投递、幂等消费（dev/13 §3）。目标：统一 outbox 表结构与派发器（common/outbox），各领域只注册事件类型与处理器；派发失败进重试与死信登记，逐店隔离扇出沿用 StoreTask。
- 跨域协作（orders→wallet 结算、orders→workforce 工资事件、growth 权益发放）优先经 outbox 事件＋幂等消费，替代直接跨 app 函数调用；同库强一致场景仍用服务接口＋同事务。

### 6.2 周期任务收敛

61 条 beat 任务分三类治理：

1. **业务时钟**（预约到期、抢单窗口、收益冻结、售后计时）：保留独立任务，统一持久化时钟来源（M3 贯通项），禁止内存时钟。
2. **补偿扫描**（30+ 通知恢复、授权重算重试等 60s 扫描）：合并为通用补偿扫描框架——单任务按事件类型注册表逐店扫描 outbox/待恢复记录，替代每类一条 beat；合并须逐类回归通知恢复专项后切换。
3. **聚合快照**（配额用量日快照、平台汇总）：新增逐店只读采集任务，失败标注缺失不补零。

### 6.3 实时通道

- 保留 StoreChannelLayer 组名哈希隔离；OrderConsumer 演进为按域订阅的统一消费者（订单/钱包/通知/咨询事件经 outbox→WS 推送），组命名规范 `account_{id}`、`order_{id}`、`staff_pool` 等登记成契约（dev/13）。
- WS 只做增量提醒，客户端必须回读 REST 确认（已确认）；token 鉴权沿用 channels_middleware，断线重连校验凭据版本。

## 7. API 治理

1. **版本化**：新契约统一 `/api/v1/` 前缀；现有 `/api/<app>/` 路由保留至前端完成切换（前端由其他开发负责，切换排期须协调），双路由期间同一视图实现，不做行为分叉。
2. **错误契约**：统一错误码结构（code/message/detail/next_step），权益拒绝返回 entitlement 原因码；未知金额/统计缺失显式标记，不返回 0。
3. **幂等与并发**：写操作统一 `client_request_id`（同键异容 409）；乐观版本校验沿用现有版本字段模式。
4. **权限注册表**：`模块:动作` 注册表迁至共享底层，按域分文件；平台权限（stores/platform_permissions）与店内权限分离不变。
5. **契约文档**：以 dev/09 工作包契约为准，接口变更同步 [PRD 08 页面接口追踪](../prd/08-页面接口追踪.md) 与前端开发；OpenAPI schema 导出作为【设计提案】纳入 R5。

## 8. 配置与环境分层

- `config/settings.py` 拆为 `config/settings/`（base.py、dev.py、test.py、mysql_test.py、prod.py），保留 `config.settings` 与全部 *_test_settings 入口兼容（CI 与 406 个测试文件依赖现有入口，入口不变）。
- `config/database.py` embedded/managed 双模式保留；managed 强制非 root＋TLS CA（已实现）。
- 密钥全部 env/外部注入，不入库；发布前密钥清单核验（26 规划 C6）。

## 9. 非功能设计（NFR）

| 维度 | 目标 | 落点 |
| --- | --- | --- |
| 可观测性 | 结构化日志（已有 stdout JSON＋脱敏）之上增加指标（HTTP/WS 延迟错误率、Celery 队列积压、outbox 滞后、逐店健康）、错误追踪、告警（证书/磁盘/备份/任务失败） | M8，26 规划 C2；埋点随 R6 |
| 安全 | fail-closed 权限（已有）、租户上下文 fail-fast（R1）、审计不可篡改、授权码摘要化（已有）、限流（登录/短信已有，写接口分级为提案）、依赖漏洞升级（前端 14 项交前端团队） | R1/M6/M8 |
| 容量 | 160 人持续容量＋50RPS/30min 云端压测定主机规格；每店一库天然分片，控制面为聚合瓶颈点，聚合走日快照不实时跨库 | M8，26 规划 B2 |
| 备份容灾 | 异地副本＋告警收件人＋自动清理；RPO≤5min/RTO≤2h 演练；物理断库演练与 M5 合并 | M8，26 规划 C1/C3 |
| 降级 | 控制面故障签名快照营业（§3）；单店库故障逐店标注不影响他店；媒体对象存储迁移 | M5/M8 |

## 10. 重构波次总览

详细执行见 [REFACTORING_PLAN.md](REFACTORING_PLAN.md)。原则：**绞合式渐进，不推倒重来**；每波次独立可回退、测试全绿才进下一波；数值行为以 characterization tests 锁定；与 26 规划 M 阶段和 dev/09 E 工作包对齐，不另造进度口径。

| 波次 | 内容 | 对齐 |
| --- | --- | --- |
| R1 隔离加固 | context fail-fast、建店七态、权限注册表下沉 | M1/M2，E01/E02 |
| R2 领域解耦 | orders↔wallet 单向化、金额内核、console/orders/stores 子包拆分 | M3/M4，E03–E08 |
| R3 权益统一 | entitlement 服务、开关三层治理、发放入口拦截 | M5，E02/E06 |
| R4 事件任务 | outbox 统一派发、补偿扫描合并、持久化时钟贯通 | M3/M5，E04/E10 |
| R5 API 治理 | v1 前缀、错误契约、契约追踪同步 | M6/M7，E09/E10 |
| R6 配置观测 | settings 分层、指标埋点、告警接入 | M8 |

## 11. 风险与非目标

- **风险**：子包拆分触碰 489 文件的导入路径（缓解：兼容导出＋全量测试门禁）；金额内核替换引入口径漂移（缓解：characterization tests 逐分比对，口径争议停手上报）；补偿合并遗漏通知恢复边界（缓解：逐类专项回归后才退役旧开关）；前端契约不同步（缓解：双路由过渡＋契约追踪表）。
- **非目标**：不拆微服务、不换 ORM/数据库、不做旧数据迁移、不做视觉重设计、不在首版引入多活高可用与线上支付。

## 许可与版权

本目录中由青岛声浪传媒有限公司依法享有权利的原创代码、文档与设计属于闭源专有内容，适用仓库根目录的 [LICENSE](../../LICENSE)。第三方组件、字体和图标仍适用各自原有许可证和版权声明，参见[第三方说明](../../THIRD_PARTY_NOTICES.md)。

Copyright (c) 2026 青岛声浪传媒有限公司. All rights reserved.