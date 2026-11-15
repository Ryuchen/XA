# SaaS 产品模块与三端前端闭环

确认依据：2026-11-11 用户本轮要求。本轮只修改三端前端及产品文档，不修改后端、数据库和部署配置。本文新增接口为待对接契约，不代表后端、资金或业务已验收。首版、独立业务库、域名隔离、微信登录和 License 规则继续执行。

## 1. 产品与模块边界

平台统一部署和运营，支持电竞俱乐部入驻。平台人员使用 `admin.esports.club` 的独立平台身份统一管理俱乐部、订单、派单和产品模块。店内客服、俱乐部经营者与陪玩完成本店业务。聚合不合并客户、钱包或业务库，也不代表可跨店任意派单。商业化是产品目标，不能用演示页面或假定营收认定商业成功。

| 模块 | 交付/购买 | 业务范围 | 私人定制 |
| --- | --- | --- | --- |
| ADMIN 后台管理端 | SaaS 固定模块，不单独拆卖 | 本店派单、订单、人员、售后、财务及经营，平台独立管理入口 | 平台统一 B 端规范，不接受单店覆盖流程或权限 |
| PROVIDER 陪玩端 | SaaS 固定模块，不单独拆卖 | 本人接单、报名、履约、报单、收益与提现 | 与后台共用 B 端字体、状态色、组件及交互规范 |
| BOSS_H5 老板端 | 独立可购模块 | 微信登录、服务选购、自主下单、本人订单、权益及售后 | 可定制品牌、客户侧展示、布局及内容，不侵入固定模块和公共业务规则 |

H5 品牌配置与定制需求受理分开。本轮面板支持名称、主色、欢迎语、客服入口名称及展示密度；更深的客户侧页面定制通过需求单确认范围，不接受自定义 JS、任意 CSS 或后台/陪玩配置注入。前端提交使用白名单。品牌变量只作用 `.boss-app`，后台与陪玩端不读 H5 品牌配置。

SaaS Basic/Pro/Ultra 的价格、配额和有效期见 [License](33-SaaS门店授权与License.md)。License 和 H5 购买独立，三档年费不能被解释为 H5 模块报价。H5 售价、定制费、购买周期和交付期限未确认，显示按需报价；需求提交不代表付款、激活或上线。本轮不新增在线付款。

未购 H5 的俱乐部继续使用固定后台进行代单、派单与陪玩履约。模块结束时既有订单、售后、钱包及权益仍可处理。能否创建新的 H5 订单由服务端 `can_create_order` 决定，前端不凭日期或套餐名推断；License 到期仍保留既有用户核心业务的规则不变。

## 2. 入口、导航与角色

| 身份 | 入口/导航 | 范围 | 授权边界 |
| --- | --- | --- | --- |
| 平台超级管理员 | 统一后台 → 平台总览 / 入驻俱乐部 / 订单与派单 / 模块与购买 / 平台财务 / 平台账号 / 账号安全 | 获授权俱乐部的入驻、配置、模块和业务追踪 | 独立 `SUPER_ADMIN` 且 `scope_slug=*`，逐动作服务端核验 |
| 平台订单人员 | 统一后台 → 订单与派单 | 显式授予订单权限的俱乐部 | 对接新增 `ORDER_VIEWER/ORDER_OPERATOR`，须后端确认；财务授权不隐含订单授权 |
| 平台财务 | 统一后台 → 总览 / 财务 / 安全 | 已授权提现、押金缺口等现有功能 | 不自动显示俱乐部、模块或订单管理 |
| 俱乐部老板/店长 | 本店后台 → 我的工作台 / 获授权经营页面 / 模块与品牌 | 本店经营及产品配置 | `module:view` 查看，`module:manage` 操作；消费老板账号不获得经营权限 |
| 客服 | 本店后台 → 我的工作台 / 订单与派单 / 获授权审核及咨询 | 本店客户、服务、代单和状态跟进 | 继续使用 `order:view/order:dispatch` 等实际权限 |
| 消费老板 | 本店域名 `/boss/` → 首页 / 陪玩 / 订单 / 消息 / 我的 | 本人当前店的订单、权益与资金 | 微信快捷登录，不提供密码或独立手机号登录 |
| 陪玩 | 本店域名 `/player/` → 接单 / 报单 / 消息 / 钱包 / 我的 | 本人订单及获准公开订单 | 不读取其他人员私有资料、员工工资或跨店数据 |

后台登录默认进入我的工作台，平台身份进入平台总览；显式目标重定向保留。后台未登录不选店，业务库由真实认证结果确定，更换店内身份重新登录。老板/陪玩不增加门店切换。声浪 `sl.esports.club`、兴安 `xa.esports.club` 的四个业务入口保持不变。

平台使用独立平台 API，不借用店内 JWT。订单唯一标识为 `store_slug + id`；老板、服务和陪玩候选均限定目标店。平台代单先选目标俱乐部、老板、服务与数量，再核价、记录老板确认并提交。动作同时核验独立授权与服务端 `allowed_actions`。

## 3. 统一状态与双来源流转

| 维度 | 字段/值 | 统一含义 |
| --- | --- | --- |
| 订单 | `status`: PENDING / GRABBED / IN_SERVICE / COMPLETED / CANCELLED | 待接单 / 已接单 / 服务中 / 已完成 / 已取消 |
| 支付 | `payment_status`: UNPAID / PAID / PARTIALLY_REFUNDED / REFUNDED | 未支付 / 已支付 / 部分退款 / 已退款；DIRECT_TEST 显示待核对 |
| 派单进展 | 路径、选人要求、分配事实、期限 | 等待客服 / 公开接单 / 等待选人 / 已分配 / 已结束；期限已到只提示待处理，不在浏览器取消 |
| 售后 | `aftersale_status`: OPEN / RESOLVED / REFUNDED / NONE | 处理中 / 已结案 / 已退款 / 无售后；缺失不猜测为无售后 |
| 结算 | `settlement_status`: WAITING / SETTLED / REVIEW | 待整单结算 / 已结算 / 收益待核对；本人到账继续看个人份额及钱包 |
| 下单来源 | 新增 `order_source`: STAFF_MANUAL / BOSS_H5 / RESERVATION / GIFT | 客服代下单 / H5下单 / 预约转单 / 礼物单，缺失显示来源待核对 |
| 分配路径 | `dispatch_route`: STAFF / PUBLIC | 客服派单 / 公开接单；H5 也可走客服安排，不能由 STAFF 猜测客服创建 |

| 环节 | 客服代单链路 | 老板 H5 链路 | 陪玩及跨端口径 |
| --- | --- | --- | --- |
| 创建和报价 | 本店老板、服务、资金选择及确认记录 | 本人选服务、选人及适用优惠/积分 | 创建不是付款、接单或收入到账 |
| 支付 | 核验真实记账 | 核验真实钱包/支付结果 | 未核实付款不显示为派单成功 |
| PENDING | 客服安排或既定公开流程 | 公开接单、试音选人或客服安排 | 报名、抢单提交、组队和群发均不等于分配成功 |
| GRABBED | 查看实际陪玩及分配记录 | 同一状态与详情 | 本人核对入场要求及获准动作 |
| IN_SERVICE | 跟进履约、房间、转单与凭证 | 查看进度与适用售后 | 按原规则开始、报单或完成 |
| COMPLETED | 实际完成与审核分别核对 | 查看结果、评价与售后 | 完成不代表收益结算或可提现 |
| 取消/退款/售后 | 分别保留取消、退款流水及工单 | 查看实际退款构成和到账 | 取消不等于退款；部分退款不抹去履约事实 |

礼物、预约、特殊玩法房间、多人分段与协商沿用专项 PRD，不新增通用完成动作绕过流程。老板端不展示内部结算维度；陪玩及后台保留整单状态和本人收益信息。时间线只显示已返回的有效时间戳或平台 `events`，不补造中间阶段或时间。

共享实现为 `fronted/shared/src/order-lifecycle.ts` 及 `OrderJourney/OrderSource/OrderSyncBar`。原有动作权限、报价、资金幂等与审核继续使用现有逻辑。

## 4. 反馈、同步与结果核对

- 现有 WebSocket 用于失效后重读 REST，不能直接代替业务事实。页面恢复、重新联网与30秒前台只读刷新补偿漏事件；后台标签页不轮询。
- 显示最近核对时间；断网说明可能变化，读取失败明确为上次结果；刷新不重放写请求。
- 模块需求、品牌保存、平台代单/指派携带稳定 `client_request_id`；有版本的动作携带 `expected_version`。提交期间锁按钮，成功重读。
- 网络错误、5xx、409等结果不明时，按身份/门店/对象在 `sessionStorage` 保留请求核对标记，不存密码、token或表单内容。关闭或刷新不解锁。
- 仅同店同请求的终态回执 APPLIED / REJECTED 可以解除待核对。PROCESSING / NOT_FOUND 不表示失败，不能据此重试扣款/派单。
- 未对接能力由服务端 capability 控制调用，界面明确尚未开放并联系平台。不生成假授权、假订单或假成功；隐藏菜单不能代替服务端鉴权。

## 5. 待后端对接契约

以下接口沿用 `{code:0,data:...}` 和现有错误格式。本轮不实现服务端；能力、鉴权、持久化与幂等可用后才声明 capability。

| 来源 | 新增字段 | 用途 |
| --- | --- | --- |
| `/api/admin/platform/profile` | capabilities: SAAS_MODULES_V1 / PLATFORM_ORDERS_V1 / PLATFORM_DISPATCH_V1 | 模块、聚合订单与写动作能力 |
| `/api/stores/current/` | capabilities: SAAS_MODULES_V1 / BOSS_H5_BRANDING_V1 | 当前认证/域名门店的模块及品牌 |
| 同上 | `boss_h5:{entitlement,branding}` | 仅已发布的公开品牌与下单能力，不返回合同、内部备注或授权码 |
| 店内 profile | `module:view/module:manage` | 模块查看与管理，不能代替平台权限 |
| 现有三端订单列表和详情 | `order_source`、已有状态、`dispatch_route`及事实时间 | 来源由服务端创建入口不可变记录，不能相信客户端自报创建者 |

### 5.1 模块购买与私人定制

| 范围 | 方法/路径 | 请求或返回 |
| --- | --- | --- |
| 店内模块 | GET `/api/saas/modules/` | 当前店 ModuleContract |
| 平台模块 | GET `/api/admin/platform/saas/modules/{store_slug}` | 授权店 ModuleContract |
| 店内需求 | POST `/api/saas/modules/requests/` | `{client_request_id,expected_version,kind:PURCHASE或CUSTOMIZATION,note,branding?}` |
| 平台需求 | POST `/api/admin/platform/saas/modules/{store_slug}/requests` | 同上，真实平台操作者审计 |
| 店内品牌 | POST `/api/saas/modules/branding/` | `{client_request_id,expected_version,branding}` |
| 平台品牌 | POST `/api/admin/platform/saas/modules/{store_slug}/branding` | 同上 |
| 店内回执 | GET `/api/saas/modules/receipts/{client_request_id}/` | SaasReceipt |
| 平台回执 | GET `/api/admin/platform/saas/modules/{store_slug}/receipts/{client_request_id}` | SaasReceipt |

ModuleContract：`store_slug,version,read_at,h5,branding,allowed_actions,requests[]`。

- h5：status=NOT_PURCHASED/ACTIVE/SCHEDULED/EXPIRED/SUSPENDED，starts_at、ends_at、can_create_order、can_customize。allowed_actions 为 REQUEST_PURCHASE / REQUEST_CUSTOMIZATION / SAVE_BRANDING。
- branding 白名单：display_name（1–30字）、primary_color（#RRGGBB）、welcome（≤60字纯文本）、support_label（1–12字）、density=comfortable/compact。服务端重新校验，不允许固定模块设置注入。
- requests：id、client_request_id、kind、status、created_at、note；状态 SUBMITTED/REVIEWING/APPROVED/REJECTED/DELIVERED。写入成功返回最新 ModuleContract。
- 品牌保存不擅自表示公网发布；公开端只读取已发布版本。H5 需求不是付款凭证或授权结果。

### 5.2 平台统一订单与派单

| 方法/路径 | 契约 |
| --- | --- |
| GET `/api/admin/platform/orders` | 查询 store/source/status/payment/keyword/page/page_size；返回 results/total/complete/generated_at/missing_stores |
| GET `/api/admin/platform/orders/{store_slug}/{id}` | 详情、version、allowed_actions、events[]；严格核验组合身份 |
| GET `/api/admin/platform/orders/{store_slug}/{id}/candidates` | `{results:[{id,name}]}`，仅本店合资格候选 |
| POST `/api/admin/platform/orders/{store_slug}/{id}/assign` | `{client_request_id,expected_version,provider_ids,remark}`；ASSIGN动作与独立派单授权同时满足 |
| GET `/api/admin/platform/orders/{store_slug}/dispatch-options` | 本店 customers/services 的 id/name 列表 |
| POST `/api/admin/platform/orders/{store_slug}/dispatch/quote` | `{customer_id,service_id,quantity,dispatch_route:STAFF}` → `{token,amount,currency_name,summary,expires_at}` |
| POST `/api/admin/platform/orders/{store_slug}/dispatch` | `{client_request_id,quote_token,boss_confirmation,order_source:STAFF_MANUAL}` → 真实订单；来源仍由服务端确认 |
| GET `/api/admin/platform/orders/{store_slug}/receipts/{client_request_id}` | SaasReceipt |

平台订单字段：`id,store_slug,store_name,order_no,service_name,customer_name,provider_names[],currency_name,version,allowed_actions[]`及第3节状态和事实时间。events为`id,at,title,actor?,remark?,version?`，来自真实日志。`amount`沿用当前接口最小金额单位，由共享金额库格式化，不把人民币与店币混用。

SaasReceipt：`{client_request_id,store_slug,status:APPLIED/REJECTED/PROCESSING/NOT_FOUND,message?}`。回执关联真实事务/幂等记录，未找到不宣告失败。报价须保存真实折扣、资金选择和确认信息，平台操作者不能被冒写为店员。后端复核报价有效期、合资格人数、接单限制、余额及并发，事务失败完整回滚。

聚合总数、分页与时间为真实服务端结果。店库不可用显示部分结果和缺失门店，不以零补齐；浪花和星辉不作为同币余额合并。平台写入仅落目标店，不设计跨库资金挪用。

## 6. 视觉、适配与参考

2026-11-11 最新筛选设计批注：所有列表筛选融合对应表格容器，位于列表标题与表头之间；按常用/低频分配，更多条件仅展开一层，合并同维度 Tab/下拉/复选重复入口。订单队列继续使用原参数及映射，固定待接单队列时状态按原实际查询口径展示，不制造矛盾选择。全局分析日期/月度只在陪玩业绩、薪资子页面和ROI等相应页头右侧融合展示；报单/提现/流水/成本等列表检索保留在表内。外部积分登记减少装饰性卡片层级，业务对象选择与页面/模态框下拉统一小圆角。仅前端布局、文案与交互修订，不新增接口、权限或记账规则；范围和验证见 [筛选修订记录](../../fronted/FILTER-DESIGN-REVIEW.md)。

2026-11-11 后台设计原则实施：在现有业务和接口上完善导航定位、页头语义变量、可见筛选标签、表格无障碍与局部滚动、加载/空/异常共用表内展示、未知资金与统计，以及相关详情/试算的固定区块。VIP 积分倍率列在配置读取前后保持一致，原编辑和提交能力不变。59 个导航入口在1366×1024逐页复核，原筛选和表头稳定；8项导航/刷新测试、构建、UI读态回归与后台WSL/Docker热更新通过。13个入口实际接口读取异常仍待后端排查；部署脚本完整Verify因后端监听归属保护未通过，未改动环境或后端。本轮无新增接口需求。覆盖与限制详见 [后台实施记录](../../fronted/ADMIN-DESIGN-REVIEW.md)。

2026-11-11 全项目设计原则增补：用户指定 [VoltAgent/awesome-design-md](https://github.com/VoltAgent/awesome-design-md) 纳入正式设计参考库，三端后续页面、组件和评审统一遵循 [项目设计入口](../../DESIGN.md)、[系统设计标准](../DESIGN_SYSTEM.md) 与 [参考选用记录](../design/REFERENCE-LIBRARY.md)。设计决策须落实到语义 token、共享组件、完整状态及目标视口验收；后台与陪玩固定模块保持一致，老板端购买/定制不得侵入固定模块和公共业务。具体外部品牌分析只采用已核对且适合当前任务的模式，不作为交易或权限依据。用户已补充微信文章的十二项要点，现纳入[十二项页面交互原则](../DESIGN_SYSTEM.md#8-saas-页面交互的十二项原则)。首用清单读取本店设置结果，套餐说明区分固定模块与独立购买项，服务评价采用本套餐公开订单评价。相关呈现使用已有数据源。

2026-11-11 后台读态逐页验收：加载、成功空、首次读取异常与有数据时直接使用同一套真实页面结构，筛选、列和区块标题保持一致，骨架仅在原区块内显示，不再使用独立异常页结构。金额、余额、数量和统计的未读取值使用 —，成功空结果才显示真实 0；未知开关和配置不能表示为关闭或未配置。钱包资金/账户、薪资预览/规则/确认记录、通行证规则/购买记录、费率/历史、字典目录/项目、平台汇总/明细分别按来源判定，成功来源不被另一个失败来源覆盖。已有同查询快照刷新失败时保留并标明，切换门店和筛选不得显示上个查询结果。必需数据未读取时禁用相关资金/审核/保存动作，既有权限、请求与业务处理不变。本轮无新增接口；当前 13 个门店入口实际出现读取异常，待后端对接方排查，前端不以假数据或 0 掩盖。逐页清单、异常入口与验证边界见 `fronted/PAGE-STATE-AUDIT.md`。

2026-11-11 后台页头：参照用户指定的“运营内容管理”页头，统一门店与平台业务页面的标题、简短用途说明、既有操作和真实指标。用途说明依据本套 PRD 及当前页面功能组织，优先解释“本页处理什么”；权限、金额与不可逆后果仍留在相关业务操作处。59 个门店导航页与 7 个平台页具备用途文案，首次读取失败仍展示当前页标题、用途和原有错误恢复入口；加载时保留真实页面结构，骨架限定在数据区块内。无新增后端接口或统计需求，不为统一页头制造演示数字。说明与验证记录见 `fronted/PAGE-HEADERS.md`。

固定 B 端沿用 React + Tailwind CSS4 + shadcn、共享 MiSans、海洋蓝/奶白和深浅色token。任务结构按角色不同，字体、语义色、表单、反馈及小圆角统一。后台最小1366×1024，40px控件；陪玩手机保留44px触控及底部导航，桌面保留既有导航。

老板端保持五项手机导航及桌面布局；自定义品牌只覆盖客户侧名称、主色、欢迎语、客服入口及密度，自动选可读前景，不改变语义状态色。预览标注未发布，不放真实余额或假业务。长名称/错误可展开，表格允许局部横滚，焦点、加载、空与失败可见。

用户设计参考 `https://mp.weixin.qq.com/s/OVLt4Qg3cr0XkKPw20Ft6g` 本轮工具无法读取，未假称已学习其细节。沿用现有原型，并检索 [React/Vite shadcn Dashboard](https://github.com/shadcndashboard/shadcndashboard) 核对导航、表单与列表组织，不复制其组织切换或业务规则。

## 7. 验收边界

2026-11-11 业务导航归类补充：薪资四页统一使用侧边栏入口，移除页面内重复的跨页切换。原“积分礼品与会员”聚合页的 13 项功能按业务拆为独立页面：商品与营销承接积分礼品、幸运转盘、赠礼附送和运营内容（成就、签到同归营销活动）；客户管理仅承接 VIP 等级、会员与积分设置、外部积分登记；客服管理承接权益交付；财务管理承接充值档位；陪玩管理承接冠名展示；系统管理承接事件播报规则、播报记录及 KOOK 积分连接状态。原有待办和操作核对入口指向新页面，状态等筛选参数保留，实时事件继续按业务页刷新。现有查询/操作权限与后端接口保持不变，挪到财务或系统菜单不自动授予其其他页面权限；会员设置中的字典配置仍按原字典权限显示。本次无新增后端对接需求。详细功能映射及验收记录见 `fronted/NAVIGATION-UPDATE.md`。

2026-11-11 后台导航补充：按用户确认的侧边栏对照稿，一级栏固定 80px、二三级面板 216px；图标缩为 16px，二级分组可折叠、三级页面缩进并展示蓝色当前态。搜索改为固定入口和独立结果面板，仅搜索当前角色可访问功能。展开、收缩及浮层共用同一菜单、图标和一级操作坐标；切页保留展开偏好。该项只改前端导航呈现，不修改订单、派单、资金、门店切换及鉴权逻辑，不新增后端对接需求。实施记录见 `fronted/SIDEBAR-UPDATE.md`。

1. 执行类型检查、三端构建及共享测试，验证来源/路径独立、状态不混淆、品牌白名单和回执归属。
2. 浏览器验证后台1366×1024，老板/陪玩390×844与1366×1024、深浅色、导航、列表/详情及溢出。
3. 未接入接口用独立 UI 契约样例检查请求、权限、错误恢复和样式隔离，明确非真实业务验收。测试不允许业务写请求到达实际后端。
4. 本机登记隔离环境只读核验，不清库、重建、导入客户/资金或发送真实消息。
5. `deploy/dev.ps1 Verify -Hmr` 验证五入口、租户绑定、WSL/Docker和浏览器HMR。前端改动不需重启后端或重建镜像。
6. 后端接入后分别完成两种来源从报价、支付、分配、履约、审核/售后到结算的真实联调；核验并发、幂等、回滚、跨店拒绝和License到期保留核心业务。本轮不宣称已通过。

执行记录、限制和截图索引见 `fronted/SAAS-DELIVERY.md`。
