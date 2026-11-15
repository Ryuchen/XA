# 后台业务导航归类与去重

2026-11-11，按浏览器评论调整；仅前端，不改接口、记账、权益规则、提交处理器或数据库。

## 页面归属

| 一级模块 | 二级分组 | 页面（新路径） | 原功能 |
| --- | --- | --- | --- |
| 商品与营销 | 商品管理 | 积分礼品 `/club-rewards` | rewards |
| 商品与营销 | 营销活动 | 幸运转盘 `/lotteries`、赠礼附送 `/gift-benefits` | lotteries / giftBenefits |
| 商品与营销 | 内容管理 | 运营内容 `/club-content` | content |
| 客户管理 | 会员与积分 | VIP 等级 `/vip-tiers`、会员与积分设置 `/member-settings`、外部积分登记 `/external-points` | tiers / settings / external |
| 客服管理 | 权益履约 | 权益交付 `/club-claims` | claims |
| 财务管理 | 充值配置 | 充值档位 `/recharge-packages` | packages |
| 陪玩管理 | 权益展示 | 陪玩冠名 `/provider-namings` | namings |
| 系统管理 | 事件播报 | 播报规则 `/broadcast-rules`、播报记录 `/broadcasts` | rules / feed |
| 系统管理 | 外部集成 | KOOK 积分状态 `/kook-points-status` | kook |

成就配置、签到打卡由“客户→会员活动”移至“商品与营销→营销活动”；现有优惠券和促销活动同组。首页轮播和公告归内容管理。群派单配置与 KOOK 状态归外部集成，统一字典仍归门店设置。

按业务对象区分配置、执行、记录：积分商品库存归商品，权益实际交付归客服；预存档位归充值配置，资金流水保持原入口；冠名展示归陪玩；KOOK 连接状态属于外部接入，不是会员等级功能。

## 导航与权限

原聚合页的十三项横向切换移除，每页从对应侧边栏进入；薪资方案、员工安排、业绩归属和月度核算四页移除重复导航。签到等同一业务内部的规则/进度视图保留。

页面映射与查询权限集中在 `admin/src/lib/operation-navigation.ts`。查询权限沿用 `engagement:view`，权益交付沿用 `engagement:fulfill`，外部积分登记沿用 `engagement:points`；编辑、发放、交付和字典配置继续由原子页面权限检查执行。菜单分组不增加权限，也不以财务/系统模块的通用权限替代旧权限。

原聚合 URL 只做前端跳转，移除 tab 参数并保留 status 等任务筛选；越权或无效 tab 进入 403。工作台权益待办、操作结果核对链接已更新到独立页面。REST 端点、请求内容与业务子组件保持原样；实时事件按新路由关联原订阅类型。

## 验证

- 管理后台 TypeScript 检查与容器内生产构建通过；新增映射、导航和实时映射 eslint 通过；机械设计扫描无发现。
- 8 项导航与实时回归通过：客服交付权限、外部积分权限、越权入口拒绝、待办状态/筛选保留、入口唯一、功能模块归属、各页实时事件不串用以及既有实时行为。
- 对照修改前快照，业务子组件自 `PointsRedemptionConfigIfPermitted` 起逐字一致（仅设置页标题澄清）；薪资各页面内容逐字一致，仅去除 `SalaryLinks`。未改核心请求、提交或状态流转。
- 浏览器检查：薪资重复导航消失；会员页不再出现十三项切换；商品、权益交付与系统集成入口和面包屑匹配。Vite 热更新沿用登记的 WSL/Docker 服务。
- 截图、构建日志和源文件修改前快照在 `.tools/navigation-reclassification-20261111/`。只读浏览器检查未执行新增、编辑、派发、充值或资金操作。

`deploy/dev.ps1 Status` 显示三端与网关容器健康；后端监听归属检查仍提示 `Managed backend does not exclusively own its recorded loopback listener` 并停止，未更改任何进程。本次只确认前端容器构建与浏览器 HMR，不将其记为部署总体验证通过。

本次不新增后端需求；现有页面展示的“待开放”、接口失败和外部集成未配置状态保留，不因入口拆分宣称功能已对接。
