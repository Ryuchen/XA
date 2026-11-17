# 遗留旧端脚本外科式清除审计（2026-11-17）

日期：2026-11-17／18；状态：**执行记录（已完成；pass2 隔离区处置待用户决定）**。

## 1. 背景与用户决定

用户目标"继续清除整个项目中旧有和无关代码"。2026-11-15 审计保留的 51 个混合调用遗留脚本（及后续盘点扩至 125 个顶层旧端驱动）原按 26 规划 A3"先补当前三端等价覆盖再逐项移除"排队；用户 2026-11-17 批准**外科式口径**替代：删除旧端驱动脚本与测试中的旧端 UI/node 步骤，保留后端业务断言，被移除 UI 动作产生的状态以直接 HTTP/进程内 WS 重实现；历史 UI 覆盖损失逐模块登记，不新增 React 等价覆盖（留待后续界面阶段）。同日用户另批准：design-demos/ 与 docs/design/ 全目录剔除（见 CHANGELOG 与 docs/history 归档）、首轮 7.5G 隔离区立即永久删除。

## 2. 范围与方法

- 分类基线：脚本按内容标记三分——legacy（apps/admin-web、apps/provider-vue、.tools 旧 H5 快照、miniprogram-automator）、react（fronted-*-contracts/test-runtime）、other（隔离后端 HTTP/WS、当前 React 平台浏览器、部署/验收基础设施）。legacy 125 全清；react 6 与 other 27 保留。
- 调用方核对：全文件名 grep＋basename `run_browser('name')` 补扫＋动态拼接排查；按"测试-脚本"二部图连通分量拆批（B1–B7），冻结文件范围并行代理，主会话评审 diff＋复跑核实后分批提交。
- 断言纪律：后端资金/状态机/权限/门店隔离/幂等/经后端通道的实时事件断言逐字保留；仅旧端 UI-only 检查（布局/截图/DOM/触控/声音计数/滚动宽度）与传输故障模拟删除并登记。
- 被取代业务规则裁决（证据：PRD 04/05 与现行 view 实现）：①门店级打款审批态（APPROVED+payout_reference）→ 两级审核现行规则重写（门店初审 STORE_APPROVED 不打款）；②客服 assign 接口现为有意 409 stub（新单走抢单池/老板试音选人）→ 依赖 assign 成功的旧旅程方法删除；③软作废后重评 → 唯一约束现行规则，重评断言段删除。
- 验证分层：官方 SQLite 套件前后失败集合对照（既有 WIP 红态不得新增）；库外 harness（文件 SQLite＋显式披露的 WIP 绕过：entity_prefix_for ValueError 回退、current_store 回退、APIClient 绑定门店 Host；single_asgi 共享 .tools/asgi-reservation-*.sqlite3）验证替换路径真实业务代码；浏览器实测 boss 登录 H5 接线门禁与 401 负路径；verify:frontend（便携 node 22.12＋pnpm 9.0.0）类型/共享测试/三端构建。

## 3. 批次与提交

| 批次 | 范围 | 提交 | 核实 |
| --- | --- | --- | --- |
| 上轮固化 | 小程序切除＋文档整合 787 文件 | 3dcb1a47 | check 0、迁移无变更 |
| 死资产首轮 | .tools 1924 项＋退役 Vue 包 | a4188a90 | 7.5G 经用户批准永久删除 |
| 二轮死代码 | 15 零引用定义＋零导入模块 | 62df11cb | AST 子集对比纯删除 |
| H5 接线 | boss LoginPage 桥接改 useH5Auth；serve-backend 替身安装器修复 | 3974f42e | verify:frontend 绿＋浏览器负路径 |
| B1 | chat/site_messages 7 测试 9 脚本 | d1940d87 | 10 tests 2 既有错误 |
| B2 | console 10 测试 11 脚本＋纯 UI 模块删除 | 8c589e33 | 失败集合不变 |
| B7 | engagement 7 测试 15 脚本 | e1954218 | 9 tests 4 既有错误 5 skip |
| B6 | stores/users/wallet 18 测试 20 脚本 | 57fa95be | 2 模块转绿、金额断言 byte-identical |
| B4 | orders-core 15 测试 | 6d3cd6d4＋60241331（23 脚本） | 49 tests 0 断言失败 |
| 提现裁决 | 两级审核重写＋3 脚本 | 618f314e＋be9724b1 | harness 3/3 |
| B3 | coupons/vip 10 测试 10 脚本 | baad8758 | 39 tests 0 断言失败 |
| sound | 双模块＋旧派单旅程裁决＋2 脚本 | 5b2fcdcc | 4 tests 3 既有错误 1 skip |
| 预约折扣 | 主会话接管 quote→convert 重写 | 449b236d | harness 2/2 |
| B5 部分 | 14 测试＋纯 UI 模块删除 | db572340＋416b2c7c（18 脚本） | 35 tests 0 断言失败 |
| B5a | 预约三模块 4 脚本 | fd803f0f | harness 1/1、1/1、2/2 |
| B5b | 协商/分段三模块 7 脚本 | 3adc5ec1 | 25 tests 失败集合不变 |
| team_gift | 漏网模块接管＋最后脚本 | 223b959e | harness 1/1 |
| gift_referral | B6 漏网门控块补清 | fb60010c | 官方 1 既有错误不变 |
| design 剔除 | design-demos＋docs/design＋prototype-preview 栈 | b12f7b9e | 链接同步、归档保留事实 |
| C3 | coupons 双模块悬空 native 调用 | d74fa398 | 28 tests 失败集合逐名一致 |
| C1 | orders 八模块（6 清理＋2 模块删除） | 5b748d52 | 14 tests 12 既有错误 2 skip |
| C2 | engagement/chat/users 六模块 | 4f560445 | 7 测试转绿、零新增失败 |
| C5→主会话接管 | checkout 双模块（env 门控＋ORM/HTTP 等价；C4/C5 代理空转被停后主会话执行） | 5fa94e94 | 官方 16 skip 不变；harness 13/16，余 3 为 grab 段待联调 |

## 3.1 全套件最终对照（2026-11-18 凌晨）

`manage.py test --settings=config.test_settings`（工作目录 services/backend）：**5333 tests，256 failures／4000 errors／509 skipped**；11-17 基线为 5341／253／4001／533。差异归因：测试数 −8＝删除的模块/方法（game_context、refund_reason、withdraw_config_permission、reservation_transfer_visibility、native_empty_tier、real_assignment_and_mute 等）；skips −24＝解除门控后转绿或转既有 WIP 红；errors −1 同源。**failures +3 全部落在本会话未触碰模块**（stores.tests_business/tests_hosts/tests_runtime、ranking/grab multistore、wallet.test_refund_income_priority 等）——程序化交集核验：本会话编辑的 109 个测试模块与 80 个失败模块交集为空，清理零断言回归成立；+3 为基线后未触碰模块的 WIP 漂移（含并行会话改动）。

## 4. 上一轮遗漏的发现与补清

兜底扫描（services 全量 node/basename 引用 vs scripts 现存文件）发现 11-17 小程序切除后仍残留 **27 处悬空 native-* 调用、分布 17 个测试模块**（orders 10、engagement 3、chat 1、users 2、coupons 2）——11-17 审计"删除名称全库 grep 零残留引用"的声称不成立，本记录予以更正。C1/C2/C3 代理＋主会话接管（checkout 双模块）已全部清理；**终态全库悬空脚本引用扫描＝0**（含 basename 与动态拼接排查）。另发现并补清 B6 漏网的 gift_referral 门控 node 块、B4 未落盘的 23 脚本删除、提现 3 脚本仅暂存未落盘。

## 5. .tools 两轮清理

- 首轮：1924 项（死快照目录/旧日志/无引用一次性脚本/退役 Vue 包）→ 隔离 → 用户批准永久删除（7.5G）。保留规则：活引用、MySQL datadir 红线、validation-backups、mtime≥11-08 近期工作、基础设施脚本。
- 二轮：脚本删除后释放的快照目录与旧证明图/beat 状态等 200 项 → `G:\XA-cleanup-quarantine-20261117-pass2`（78M，可逆，处置待用户决定）。**4 个 DPAPI 加密凭据备份（wechat-credentials-before-*.dpapi、wechat.local.env.dpapi）保留**——非代码且可能为隔离微信凭据唯一副本。
- 保留基础设施：mysql-8.0.46-winx64 发行版与全部隔离 datadir、fronted-acceptance、fronted-scaffold、runtime（便携 pwsh/node）、local-validation.env、近期日志。

## 6. 覆盖损失与待联调

- 历史 UI 覆盖损失：旧 Vue/小程序页面布局、截图、DOM/触控、声音计数、滚动宽度、对话框草稿保留、WS 驱动 DOM 刷新、离线重连不重复响铃、传输丢包模拟等，逐模块见各批次提交说明与代理报告；React 等价覆盖留待后续界面阶段（A3 口径变更已登记 26 规划）。
- 待 MySQL 层联调：grab/reject 行锁路径（sound、cursor、预约折扣转换等改写模块）在 SQLite harness 不可完成，登记待隔离 MySQL 矩阵复跑；B5b 三模块 harness 因既有 WIP（COMPLETION 结算门禁 funding_allocations 夹具缺口）BLOCKED。
- 既有 WIP 红态（identifiers 门店上下文、grab 旗标、结算夹具等）与本次清理无关，维持 11-17 审计归因；全套件最终对照见 §3.1。
- 清理中发现的既有 committed 后端 bug（非本次引入、禁改业务代码故登记待修）：①chat `ChatSessionView` `before_id` 锚点 `sender_account` deferred＋select_related 致 FieldError（B1/C2 同源 BLOCKED）；②users `/api/users/escorts/` OuterRef ValueError（C1/C2 同源）；③admin service-items PATCH 503（C1/C5 绕以 ORM）。
- 隔离 MySQL grab 竞争脚本（test_grab_receipt_mysql_isolated.py）两次复跑均内层 240s 超时；git 日志证实 grab 代码与脚本自清理开始零改动→既有环境/WIP 阻断，登记待环境修复后复跑，非清理回归。

## 7. 环境注记

本机无系统 node/pnpm/pwsh7；便携工具置于 .tools/runtime（node 22.12 自 npmmirror、pnpm 9.0.0 经 npm 镜像安装、pwsh 7.5.4 原有）；corepack 与镜像签名键不匹配故弃用 corepack；dev.ps1 需 pwsh7 启动。防管道假绿：验证命令均以退出码/结果行核实。

Copyright (c) 2026 青岛声浪传媒有限公司. All rights reserved.