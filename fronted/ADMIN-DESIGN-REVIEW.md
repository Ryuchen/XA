# 后台设计原则落实与逐页复核

日期：2026-11-11。依据根 DESIGN.md、系统设计标准与前端基线。本轮只修改 `fronted/admin` 前端展示及文档，没有修改接口、权限、订单/资金处理、数据库或部署配置。

## 实施内容

| 范围 | 本次落实 | 业务边界 |
| --- | --- | --- |
| 导航 | 保留 80px 一级栏、216px 二三级面板和 16px 语义图标；当前二级组增加定位标识；搜索标签与输入关联，补齐键盘焦点；展开/收缩/浮层共用稳定坐标 | 沿用授权后的菜单，不新增角色或可见权限 |
| 页头 | 既有蓝色页头改由后台局部语义变量管理；保留标题、用途、原操作与真实指标；补齐平台页面的具体用途 | 品牌与固定 B 端规范保持，老板端定制不参与后台样式 |
| 表格与筛选 | 商品/资源/Banner/订单/人员/权限/审计等检索补持久可见标签；修复隐藏标签样式；共享搜索选择器关联真实 label；表格补 caption 与列 scope；窄表区提供可发现的局部滚动条 | 不新增搜索参数或假装支持尚未开放的筛选，原待开放标记保留 |
| 读取状态 | 共用表内空/异常状态，保留同一筛选、表头及原区块；增加加载播报、局部错误和可用重试；接口失败后仍可调整只读筛选 | 表格写入、审核、配置等锁定仍使用原条件；不以假数据掩盖异常 |
| 资金与统计 | 钱包、流水、充值、奖惩、成本及趋势缺少金额字段时显示 —；不将不完整金额参与补零汇总 | 只保护显示，不改原账务、货币转换或交易流程；确认返回的真实 0 保留 |
| VIP | 积分倍率列常驻，配置加载前后均为 9 列；未知、未开放和真实倍率分开显示；配置失败可单独重试 | 原编辑能力、默认值、校验与提交规则保持 |
| 详情与试算 | 陪玩档案/注册、试音报名/审核、权益交付、权限编辑及费率试算保留真实区块，读取态放在区块内部；同源刷新失败保留快照 | 没有提交审核、交付、配置或资金写入来做视觉验收 |

参考采用：awesome-design-md 的“设计决策文档化、表面与文字层级、语义 token”方法，映射现有 B 端组件；未引入外部品牌字体、官网大标题、整套胶囊或暗色营销布局。来源版本与取舍见 [参考库](../docs/design/REFERENCE-LIBRARY.md)。

## 浏览器逐页结果

在现有兴安门店已登录身份、1366×1024 CSS 视口逐页检查 59 个导航入口。最终记录中全部入口均有单一主标题及用途说明，没有整页横向溢出；从初始读取到最终结果，筛选控件数量和表头列数保持一致。此处是 DOM 结构与尺寸核查，不代表所有角色、明细弹窗及真实业务链路已验收。

当前 13 个入口仍真实返回读取异常：`dashboard`、`orders`、`reservations`、`reports`、`evaluations`、`escorts`、`player-dashboard`、`service-items`、`promotions`、`transactions`、`salary-activities`、`payroll`、`operating-roi`。前端保留原结构、未知占位及恢复入口；这些接口异常须后端对接方排查，本轮未修改或绕过。

平台独立控制台页面完成源码复核和用途补充，共享状态随组件改进；现有浏览器为门店后台身份，未以另一独立平台身份执行这部分真实联调，不将代码检查当作平台权限验收。老板端和陪玩端没有在本轮改造。

实际交互另验：

- 展开/收缩前后所有一级操作的 x/y/宽高一致；图标均为 16px，展开面板和浮层均为 216px；浮层打开时正文 x 坐标不变。
- 功能搜索展示页面及二三级路径，支持无匹配提示和关闭；菜单保持原授权范围。
- 试音招募真实只读查询得到空结果，保留原筛选和 5 列表头，显示确认后的 0；清除筛选后恢复原查询。
- 注册详情在加载与读取完成后均保留申请信息、核对材料、申请与审核记录、审核处理、核对清单五个区域；权益详情保留历史快照、私密资料、处理区三部分。只打开详情，未操作审批/交付。详情异常分支完成源码复核，未人工注入后端故障。
- 检查商品页深浅色及 1366 最小视口；长游戏名称按内容换行，局部表格可横滚。默认桌面视口保存最终预览。没有声称完成物理 iPad、所有辅助技术或全系统对比度认证。
- 浏览器已捕获的最近 200 条错误日志中，没有匹配到 React 渲染、未捕获异常、引用/类型错误；接口读取失败依然如实展示。

## 构建与环境

| 检查 | 结果 |
| --- | --- |
| 后台 `npm run build`（含 TypeScript） | 通过；保留现有大 chunk 提示，不在本轮重构加载架构 |
| 8 项导航/实时刷新测试 | 全部通过 |
| 五种读取状态及独立来源 UI 回归 | 通过；额外验证异常与写操作锁定不会禁用只读筛选，表头及未知金额保持正确 |
| Impeccable 源码检测 | 无主要问题；1 项原有登录展示背景网格 advisory，属于本轮保留区域，未改造登录视觉 |
| 登记的 WSL/Docker 状态 | 三端与网关容器健康，仍使用既有 bind mount 与域名入口 |
| 后台浏览器 CSS HMR | 实测通过：临时样式标记推送和恢复都生效，商品名称未提交草稿完整保留，未整页重载；标记已从正式源码移除 |
| `deploy/dev.ps1 Verify` | 未通过：后端监听未独占其登记的 loopback listener，归属保护停止检查，未修改任何进程。未运行重启、接管或代理调整 |

因此，本轮确认后台热更新预览可用；不能将其写为完整部署 Verify 通过。检查仅涉及登记隔离环境的读取，没有清库、导入客户、发送消息或资金动作。

## 证据与复核

证据目录为 `fronted/.tools/admin-design-20261111/`，包含逐页 JSON、导航坐标、HMR 标记与草稿保留记录、构建和测试日志，以及搜索、空查询、深色和详情截图。该目录是本机验收产物，不作为运行时代码依赖。

后续页面继续使用 `PageHeader`、`DataPageState`、`DataTable`、`SourceDirectoryCard`、`DataValue`、`DataRegion` 和同一 `TablePlaceholder`；需要新模式时先补共享组件与完整状态，避免按页面复制另一套异常布局。

## 59 个入口清单

下表“加载→结果”表示初始/最终的表头列数与筛选控件数一致；未使用列表的页面仍核查主标题、用途和现有区块。缺少具体查询的角色、授权变化及其他业务分支仍须后续按 PRD 单独联调。

| 页面 | 主标题 | 最终读取态 | 表头列数（前→后） | 筛选控件（前→后） |
| --- | --- | --- | --- | --- |
| `stores` | 平台门店管理 | ready | 6 → 6 | 0 → 0 |
| `workspace` | 我的工作台 | 静态/独立区块 | — → — | 0 → 0 |
| `dashboard` | 运营工作台 | unavailable | 8 → 8 | 0 → 0 |
| `modules` | 模块与品牌 | 静态/独立区块 | 4/4 → 4/4 | 4 → 4 |
| `orders` | 订单与派单 | unavailable | 10 → 10 | 3 → 3 |
| `reservations` | 预约与转单 | unavailable | — → — | 1 → 1 |
| `reports` | 履约凭证审核 | unavailable | 9 → 9 | 8 → 8 |
| `evaluations` | 订单评价 | unavailable | 7 → 7 | 10 → 10 |
| `escorts` | 陪玩档案 | unavailable | 8 → 8 | 4 → 4 |
| `player-dashboard` | 陪玩业绩 | unavailable | 5/5/5 → 5/5/5 | 2 → 2 |
| `audition-links` | 试音招募 | ready | 5 → 5 | 3 → 3 |
| `audition-signups` | 试音报名审核 | ready | 5 → 5 | 4 → 4 |
| `provider-registrations` | 注册审核 | ready | 6 → 6 | 3 → 3 |
| `escort-levels` | 陪玩等级 | ready | 5 → 5 | 2 → 2 |
| `provider-passes` | 通行证 | 静态/独立区块 | 10 → 10 | 3 → 3 |
| `deposit-rules` | 押金与结款规则 | ready | — → — | 5 → 5 |
| `dispose-records` | 奖惩记录 | ready | 5 → 5 | 4 → 4 |
| `provider-namings` | 陪玩冠名 | ready | 6 → 6 | 1 → 1 |
| `users` | 老板资料 | ready | 8 → 8 | 5 → 5 |
| `boss-types` | 老板分级 | ready | 6 → 6 | 1 → 1 |
| `vip-tiers` | VIP 等级 | ready | 9 → 9 | 1 → 1 |
| `member-settings` | 会员与积分设置 | ready | — → — | 6 → 6 |
| `external-points` | 外部积分登记 | 静态/独立区块 | — → — | 6 → 6 |
| `service-items` | 服务商品 | unavailable | 8 → 8 | 4 → 4 |
| `gameplays` | 玩法配置 | ready | 6 → 6 | 3 → 3 |
| `club-rewards` | 积分好礼 | ready | 7 → 7 | 5 → 5 |
| `coupons` | 优惠券 | ready/ready | 8 → 8 | 2 → 2 |
| `promotions` | 促销活动 | unavailable | 8 → 8 | 3 → 3 |
| `lotteries` | 幸运转盘 | ready | 7 → 7 | 3 → 3 |
| `gift-benefits` | 赠礼附送权益 | ready | 6 → 6 | 1 → 1 |
| `achievements` | 成就规则 | ready | 6 → 6 | 1 → 1 |
| `checkin` | 签到规则 | ready | — → — | 5 → 5 |
| `banners` | 首页 Banner | ready | 6 → 6 | 4 → 4 |
| `announcements` | 公告管理 | ready | 6 → 6 | 4 → 4 |
| `club-content` | 运营内容管理 | ready | 7 → 7 | 4 → 4 |
| `chat` | 会话接待 | 静态/独立区块 | — → — | 1 → 1 |
| `support-cards` | 客服名片 | ready | 7 → 7 | 2 → 2 |
| `messages` | 站内消息 | ready | 6 → 6 | 2 → 2 |
| `club-claims` | 权益交付 | ready | 7 → 7 | 8 → 8 |
| `wallets` | 钱包台账 | ready | 7 → 7 | 6 → 6 |
| `recharge-records` | 充值记录 | ready | 4 → 4 | 6 → 6 |
| `transactions` | 资金流水 | unavailable | 4 → 4 | 5 → 5 |
| `withdrawals` | 提现审核 | ready | 7 → 7 | 9 → 9 |
| `payment-fee` | 微信支付费率 | ready | 5 → 5 | 5 → 5 |
| `salary-plans` | 岗位薪资方案 | ready | 9 → 9 | 1 → 1 |
| `salary-assignments` | 员工月度薪资安排 | ready | 7 → 7 | 2 → 2 |
| `salary-activities` | 薪资业绩归属 | unavailable | 7 → 7 | 2 → 2 |
| `payroll` | 月度薪资核算 | unavailable | 9/3 → 9/3 | 1 → 1 |
| `recharge-packages` | 充值档位 | ready | 5 → 5 | 1 → 1 |
| `operating-costs` | 每日投流与运营成本 | ready | 7 → 7 | 3 → 3 |
| `operating-roi` | 经营 ROI 与投流投产比 | unavailable | 3 → 3 | 2 → 2 |
| `dictionaries` | 字典与配置 | ready | 4/6/3/3 → 4/6/3/3 | 6 → 6 |
| `admins` | 后台成员 | ready | 7 → 7 | 2 → 2 |
| `roles` | 角色权限 | ready | 7 → 7 | 1 → 1 |
| `audit-logs` | 操作审计日志 | ready | 9 → 9 | 6 → 6 |
| `broadcast-rules` | 播报规则 | ready | 7 → 7 | 4 → 4 |
| `broadcasts` | 播报记录 | ready | 7 → 7 | 4 → 4 |
| `kook-dispatch` | 群派单配置 | ready | 5/5 → 5/5 | 2 → 2 |
| `kook-points-status` | KOOK 积分状态 | ready | — → — | 0 → 0 |
