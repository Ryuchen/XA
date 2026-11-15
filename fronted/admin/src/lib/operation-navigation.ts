/** Frontend destinations only. Resource endpoints and action permissions stay unchanged. */
export const operationPages = [
  { section: "rewards", path: "club-rewards", title: "积分礼品", permission: "engagement:view", keywords: ["兑换", "库存", "积分商城"] },
  { section: "claims", path: "club-claims", title: "权益交付", permission: "engagement:fulfill", keywords: ["领取", "交付", "待处理", "履约"] },
  { section: "tiers", path: "vip-tiers", title: "VIP 等级", permission: "engagement:view", keywords: ["会员", "折扣", "升级权益"] },
  { section: "packages", path: "recharge-packages", title: "充值档位", permission: "engagement:view", keywords: ["预存", "充值赠送", "档位配置"] },
  { section: "lotteries", path: "lotteries", title: "幸运转盘", permission: "engagement:view", keywords: ["积分抽奖", "奖池", "中奖"] },
  { section: "giftBenefits", path: "gift-benefits", title: "赠礼附送", permission: "engagement:view", keywords: ["礼物", "赠送权益", "累计赠礼"] },
  { section: "rules", path: "broadcast-rules", title: "播报规则", permission: "engagement:view", keywords: ["事件模板", "隐私", "展示时长"] },
  { section: "feed", path: "broadcasts", title: "播报记录", permission: "engagement:view", keywords: ["公开播报", "展示记录"] },
  { section: "namings", path: "provider-namings", title: "陪玩冠名", permission: "engagement:view", keywords: ["冠名权益", "有效期", "展示"] },
  { section: "content", path: "club-content", title: "运营内容", permission: "engagement:view", keywords: ["活动海报", "下单须知", "售后标准", "积分规则"] },
  { section: "external", path: "external-points", title: "外部积分登记", permission: "engagement:points", keywords: ["积分来源", "登记", "外部流水"] },
  { section: "kook", path: "kook-points-status", title: "KOOK 积分状态", permission: "engagement:view", keywords: ["外部集成", "连接", "频道", "心跳"] },
  { section: "settings", path: "member-settings", title: "会员与积分设置", permission: "engagement:view", keywords: ["消费积分", "生日倍率", "VIP开关", "赛季", "积分抵扣"] },
] as const

export type OperationSection = (typeof operationPages)[number]["section"]

export function operationFeature(section: OperationSection) {
  const page = operationPages.find(page => page.section === section)!
  return { path: page.path, title: page.title, keywords: [...page.keywords] }
}

/** Normalize links to the former aggregate screen without dropping task filters. */
export function resolveOperationLocation(search: string, hasPerm: (permission: string) => boolean) {
  const params = new URLSearchParams(search)
  const section = params.get("tab")
  const page = section
    ? operationPages.find(page => page.section === section)
    : operationPages.find(page => hasPerm(page.permission))
  if (!page || !hasPerm(page.permission)) return null
  params.delete("tab")
  return { pathname: `/${page.path}`, search: params.size ? `?${params.toString()}` : "" }
}
