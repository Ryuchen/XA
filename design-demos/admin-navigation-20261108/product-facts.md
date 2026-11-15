# 管理后台信息架构：已核实事实

## 本地业务事实

- 事实来源是 `fronted/admin/src/App.tsx`、`lib/navigation.ts` 及对应页面源码，不依赖对原型或旧需求的猜测。
- 当前 `navigationDomains` 有 44 个唯一页面入口；`dictionaries` 已注册页面和 `dictionary:view` / `dictionary:edit` 权限，但未进入菜单，恢复后共 45 个唯一入口。
- `pages` 中字面注册 39 个页面，另外 6 个由 `resourceSpecs` 注册：`boss-types`、`achievements`、`announcements`、`escort-levels`、`gameplays`、`support-cards`。
- 页面标题以导航配置为准。`dictionaries` 的真实页面标题是“字典与配置”，包含薪资岗位、投流渠道、运营成本类别，以及下单资料等业务规则入口。菜单可命名为“统一字典”，不能声称它是独立的全新功能。
- 初始化配置中的门店只能访问 `dictionaries` 与 `deposit-rules`。任何重排都必须让这两个页面可达，不能将初始化所需的规则藏在受限业务工作流之后。
- `stores` 需要独立的 `store.can_manage_stores === true`；普通门店管理员不能因此得到跨店权限。
- 其他页面继续使用 `permissions[path]` 鉴权。菜单只负责呈现当前账号可访问的页面，不能替代服务端权限。
- 初始化、金额、薪资、结算、派单、售后、会员权益和角色权限均已有真实业务规则，重排不得将它们改名为待开发功能或虚构新指标。

## 当前页面归属的实际问题

1. “店铺运营”混合了服务商品、玩法和促销内容；会员优惠却在“老板中心”，相关业务被拆开。
2. “群派单配置”出现在订单日常处理下，而“统一字典”完全没有菜单入口，业务配置缺少稳定归属。
3. “客服与消息”与订单评价、售后处理分属不同菜单，岗位任务的连续性不够明显。
4. 老板资料、折扣分级、VIP 与积分礼品、成就签到、优惠券需要区分客户事实、权益配置和运营活动，而不是全部塞进一个“老板中心”。
5. 陪玩的招募、入驻、试音、档案、等级、通行证、押金与奖惩已有业务链条，可以按流程或岗位重新聚合，但不能复制页面或绕过各自权限。

## 已查看的外部参考

### Linear

- 官方说明：<https://linear.app/docs/teams>，说明团队在工作区中承担工作边界，侧边栏可展开团队相关页面。
- 官方设计说明：<https://linear.app/now/behind-the-latest-design-refresh>，已读取正文并实际截取界面示意图 `references/linear-interface.png`。
- 借鉴范围：让定位、当前工作和配置保持不同层级；降低导航对内容的干扰。不能把 Linear 的团队、项目或权限模型照搬到门店。

### Shopify

- 官方导航说明：<https://help.shopify.com/en/manual/shopify-admin/shopify-admin-overview>，已读取并实际截取官方帮助页 `references/shopify-page.png`。
- 公开说明区分订单、商品、客户、营销、分析与门店设置，支持门店切换与功能搜索，角色决定员工可见功能。
- 借鉴范围：业务对象与店铺设置分家，跨对象用搜索补足。未获取其登录后的真实后台截图，不声称复刻 Shopify 界面或最新视觉。

## 参考截图与环境

- 当前菜单参考取自已有真实后台验证截图，复制为 `references/current-navigation.png`。截图只用于核对既有菜单结构，不作为本轮业务功能通过证据。
- 轮盘样张已实际查看：Web 第 4 号“糖果色凸起立体按钮游戏化”和 East 第 8 号“杉浦康平信息曼荼罗”。其原题材与数字均不进入本项目。
- 生产开发服务状态本轮未改动；菜单选择稿为独立静态 HTML，不启动额外服务。
