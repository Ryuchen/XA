import { operationFeature } from "./operation-navigation.ts"

/** Business navigation is separate from URLs and authorization. Routes remain
 * the source of permission checks, including pages nested inside a center. */
export type MenuPermission = string | string[] | undefined
export type PermissionCheck = (permission: MenuPermission) => boolean
export interface MenuRoute {
  path: string
  meta?: Record<string, unknown>
}
export interface FeatureDefinition {
  path: string
  title: string
  keywords?: string[]
}
export interface CenterDefinition {
  id: string
  title: string
  icon: string
  features: FeatureDefinition[]
}
export interface DomainDefinition {
  id: string
  title: string
  centers: CenterDefinition[]
}
export interface NavigationCenter extends Omit<CenterDefinition, "features"> {
  features: FeatureDefinition[]
}
export interface NavigationDomain extends Omit<DomainDefinition, "centers"> {
  centers: NavigationCenter[]
}
export interface SearchFeature extends FeatureDefinition {
  domain: string
  center: string
}

/** A: standard CRM/OA modules. Platform administration is deliberately not a
 * store business module; App renders it in its own authorized sidebar area. */
export const navigationDomains: DomainDefinition[] = [
  {
    id: "platform-stores",
    title: "平台管理",
    centers: [
      {
        id: "stores",
        title: "门店",
        icon: "Shop",
        features: [
          { path: "stores", title: "门店管理", keywords: ["多门店", "租户", "入口", "店名"] },
        ],
      },
    ],
  },
  {
    id: "overview",
    title: "工作台",
    centers: [
      {
        id: "overview",
        title: "数据概览",
        icon: "Odometer",
        features: [
          { path: "workspace", title: "我的工作台", keywords: ["客服", "俱乐部老板", "角色", "任务", "入口"] },
          { path: "dashboard", title: "运营工作台", keywords: ["平台总览", "统计", "营收"] },
          { path: "modules", title: "模块与品牌", keywords: ["SaaS", "购买", "老板H5", "定制", "开通", "授权"] },
        ],
      },
    ],
  },
  {
    id: "orders",
    title: "订单管理",
    centers: [
      {
        id: "orders-processing",
        title: "订单处理",
        icon: "Tickets",
        features: [
          { path: "orders", title: "订单与派单", keywords: ["派单管理", "抢单", "指派", "转单", "退款", "售后", "快捷派单"] },
          { path: "reservations", title: "预约管理", keywords: ["预约", "档期", "预约转单"] },
        ],
      },
      {
        id: "orders-aftersales",
        title: "履约与售后",
        icon: "Finished",
        features: [
          { path: "reports", title: "报单审核", keywords: ["订单审核", "结单", "完成凭证"] },
          { path: "evaluations", title: "订单评价", keywords: ["评分", "评价回复"] },
        ],
      },
    ],
  },
  {
    id: "providers",
    title: "陪玩管理",
    centers: [
      {
        id: "providers-records",
        title: "陪玩资料",
        icon: "Avatar",
        features: [
          { path: "escorts", title: "陪玩档案", keywords: ["陪玩管理", "打手", "认证", "封禁", "退押金"] },
          { path: "player-dashboard", title: "陪玩业绩", keywords: ["陪玩概览", "排行", "数据分析"] },
        ],
      },
      {
        id: "providers-recruitment",
        title: "招募审核",
        icon: "Headset",
        features: [
          { path: "audition-links", title: "试音招募", keywords: ["声卡管理", "招募链接", "报名入口"] },
          { path: "audition-signups", title: "试音审核", keywords: ["试音报名", "招募审核"] },
          { path: "provider-registrations", title: "入驻审核", keywords: ["入驻", "报名", "身份证", "实名"] },
        ],
      },
      {
        id: "providers-benefits",
        title: "等级与权益",
        icon: "Medal",
        features: [
          { path: "escort-levels", title: "等级配置", keywords: ["陪玩等级", "抽成"] },
          { path: "provider-passes", title: "通行证", keywords: ["通行证管理", "陪玩权益"] },
        ],
      },
      {
        id: "providers-rules",
        title: "押金与奖惩",
        icon: "Money",
        features: [
          { path: "deposit-rules", title: "押金与结款规则", keywords: ["单抵", "现金押金", "比例", "6:4", "60%", "押金设置"] },
          { path: "dispose-records", title: "奖惩记录", keywords: ["奖励", "处罚", "罚款"] },
        ],
      },
      {
        id: "providers-display",
        title: "权益展示",
        icon: "Medal",
        features: [operationFeature("namings")],
      },
    ],
  },
  {
    id: "customers",
    title: "客户管理",
    centers: [
      {
        id: "customers-records",
        title: "客户资料",
        icon: "User",
        features: [
          { path: "users", title: "老板资料", keywords: ["信息管理", "用户", "账号", "手机号", "封禁"] },
          { path: "boss-types", title: "老板分级", keywords: ["类型管理", "老板折扣"] },
        ],
      },
      {
        id: "customers-vip",
        title: "会员与积分",
        icon: "Present",
        features: [
          operationFeature("tiers"),
          operationFeature("settings"),
          operationFeature("external"),
        ],
      },
    ],
  },
  {
    id: "catalog-marketing",
    title: "商品与营销",
    centers: [
      {
        id: "catalog-marketing-catalog",
        title: "商品管理",
        icon: "Goods",
        features: [
          { path: "service-items", title: "服务商品", keywords: ["游玩项目", "套餐", "价目", "商品分类"] },
          { path: "gameplays", title: "玩法配置", keywords: ["玩法管理", "游戏"] },
          operationFeature("rewards"),
        ],
      },
      {
        id: "catalog-marketing-marketing",
        title: "营销活动",
        icon: "Sell",
        features: [
          { path: "coupons", title: "优惠券", keywords: ["领券", "核销", "折扣券"] },
          { path: "promotions", title: "促销活动", keywords: ["营销管理", "活动价", "折扣"] },
          operationFeature("lotteries"),
          operationFeature("giftBenefits"),
          { path: "achievements", title: "成就配置", keywords: ["成就", "成长", "消费目标"] },
          { path: "checkin", title: "签到打卡", keywords: ["全勤", "补签", "签到奖励"] },
        ],
      },
      {
        id: "catalog-marketing-content",
        title: "内容管理",
        icon: "Document",
        features: [
          { path: "banners", title: "首页轮播", keywords: ["横幅", "首页图片", "轮播图"] },
          { path: "announcements", title: "公告管理", keywords: ["公告", "通知发布"] },
          operationFeature("content"),
        ],
      },
    ],
  },
  {
    id: "support",
    title: "客服管理",
    centers: [
      {
        id: "support-service",
        title: "会话与联系",
        icon: "ChatLineRound",
        features: [
          { path: "chat", title: "会话接待", keywords: ["消息与客服", "在线客服", "聊天", "回复"] },
          { path: "support-cards", title: "客服名片", keywords: ["联系入口", "微信客服", "企微"] },
        ],
      },
      {
        id: "support-messages",
        title: "消息管理",
        icon: "ChatDotRound",
        features: [
          { path: "messages", title: "站内消息", keywords: ["消息推送", "通知", "收件人"] },
        ],
      },
      {
        id: "support-fulfillment",
        title: "权益履约",
        icon: "Finished",
        features: [operationFeature("claims")],
      },
    ],
  },
  {
    id: "finance",
    title: "财务管理",
    centers: [
      {
        id: "finance-accounts",
        title: "资金管理",
        icon: "Wallet",
        features: [
          { path: "wallets", title: "钱包账户", keywords: ["老板钱包", "打手钱包", "钱包管理", "余额", "充值", "调账"] },
          { path: "recharge-records", title: "充值记录", keywords: ["账目记录", "充值凭证"] },
          { path: "transactions", title: "资金流水", keywords: ["流水记录", "收支", "账务"] },
          { path: "withdrawals", title: "提现审核", keywords: ["打款", "结款", "收款账户"] },
          { path: "payment-fee", title: "微信支付费率", keywords: ["微信支付", "商户费率", "手续费"] },
        ],
      },
      {
        id: "finance-payroll",
        title: "薪资管理",
        icon: "Money",
        features: [
          { path: "salary-plans", title: "岗位薪资方案", keywords: ["底薪", "绩效", "提成", "薪资说明"] },
          { path: "salary-assignments", title: "员工薪资安排", keywords: ["岗位", "人员", "月份"] },
          { path: "salary-activities", title: "业绩归属核实", keywords: ["客服", "充值提成", "售后计次", "考官"] },
          { path: "payroll", title: "月度薪资核算", keywords: ["工资", "核算", "确认"] },
        ],
      },
      {
        id: "finance-recharge-config",
        title: "充值配置",
        icon: "Money",
        features: [operationFeature("packages")],
      },
    ],
  },
  {
    id: "analytics",
    title: "经营分析",
    centers: [
      {
        id: "analytics-reports",
        title: "经营报表",
        icon: "DataAnalysis",
        features: [
          { path: "operating-costs", title: "每日投流与成本", keywords: ["广告", "投放", "投流", "运营成本"] },
          { path: "operating-roi", title: "经营 ROI", keywords: ["ROI", "投产比", "人力成本", "利润"] },
        ],
      },
    ],
  },
  {
    id: "system",
    title: "系统管理",
    centers: [
      {
        id: "system-store",
        title: "门店设置",
        icon: "Setting",
        features: [
          { path: "dictionaries", title: "快捷配置", keywords: ["统一字典", "字典与配置", "业务字典", "开店设置清单", "薪资岗位", "投流渠道", "成本类别", "初始化", "下单游戏资料"] },
        ],
      },
      {
        id: "system-permissions",
        title: "权限管理",
        icon: "Lock",
        features: [
          { path: "admins", title: "后台成员", keywords: ["客服信息管理", "管理员", "客服账号"] },
          { path: "roles", title: "角色权限", keywords: ["职位权限管理", "角色", "职位", "授权"] },
        ],
      },
      {
        id: "system-audit",
        title: "日志管理",
        icon: "Document",
        features: [
          { path: "audit-logs", title: "操作审计日志", keywords: ["审计", "操作记录", "追溯"] },
        ],
      },
      {
        id: "system-broadcasts",
        title: "事件播报",
        icon: "ChatDotRound",
        features: [operationFeature("rules"), operationFeature("feed")],
      },
      {
        id: "system-integrations",
        title: "外部集成",
        icon: "Setting",
        features: [
          { path: "kook-dispatch", title: "群派单配置", keywords: ["KOOK", "机器人", "频道", "派单设置"] },
          operationFeature("kook"),
        ],
      },
    ],
  },
]

export function applyNavigationMeta(routes: MenuRoute[]) {
  for (const domain of navigationDomains) {
    for (const center of domain.centers) {
      for (const feature of center.features) {
        const route = routes.find((item) => item.path === feature.path)
        if (!route) throw new Error(`Navigation route missing: ${feature.path}`)
        route.meta = {
          ...route.meta,
          title: feature.title,
          group: domain.title,
          center: center.id,
        }
      }
    }
  }
}

export function buildNavigation(
  routes: MenuRoute[],
  hasPerm: PermissionCheck
): NavigationDomain[] {
  return navigationDomains
    .map((domain) => ({
      ...domain,
      centers: domain.centers
        .map((center) => ({
          ...center,
          features: center.features.filter((feature) => {
            const route = routes.find((item) => item.path === feature.path)
            return (
              !!route &&
              !route.meta?.hidden &&
              hasPerm(route.meta?.perm as MenuPermission)
            )
          }),
        }))
        .filter((center) => center.features.length),
    }))
    .filter((domain) => domain.centers.length)
}

export function flattenNavigation(
  domains: NavigationDomain[]
): SearchFeature[] {
  return domains.flatMap((domain) =>
    domain.centers.flatMap((center) =>
      center.features.map((feature) => ({
        ...feature,
        domain: domain.title,
        center: center.title,
      }))
    )
  )
}

export function searchNavigation(
  domains: NavigationDomain[],
  query: string
): SearchFeature[] {
  const terms = query.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean)
  const features = flattenNavigation(domains)
  if (!terms.length) return features
  return features
    .filter((feature) => {
      const text = [
        feature.title,
        feature.domain,
        feature.center,
        feature.path,
        ...(feature.keywords || []),
      ]
        .join(" ")
        .toLocaleLowerCase()
      return terms.every((term) => text.includes(term))
    })
    .sort((a, b) => {
      const rank = (item: SearchFeature) =>
        item.title.toLocaleLowerCase() === query.trim().toLocaleLowerCase()
          ? 0
          : terms.every((term) => item.title.toLocaleLowerCase().includes(term))
            ? 1
            : 2
      return rank(a) - rank(b)
    })
}
