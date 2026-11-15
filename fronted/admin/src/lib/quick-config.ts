/** Each shortcut opens the existing owner of a setting. No configuration values live here. */
export const dictionaryTypes = [
  { code: "SALARY_POSITION", title: "薪资岗位", description: "维护员工薪资方案可选的岗位。" },
  { code: "AD_CHANNEL", title: "投流渠道", description: "维护登记投流支出时可选的渠道。" },
  { code: "COST_CATEGORY", title: "运营成本类别", description: "维护经营支出归集使用的分类。" },
] as const

export type DictionaryType = typeof dictionaryTypes[number]["code"]
export type ConfigEditor = "order-account-fields" | "capacity" | "grab" | "timeout" | "traffic" | "commission" | "game-commission" | "withdraw"
export type Shortcut = { title: string; description: string } & ({ to: string } | { editor: ConfigEditor })
interface CatalogEntry { id: string; can_view: boolean; permission: string }
export interface QuickCatalog { legacy?: CatalogEntry[]; policies?: CatalogEntry[] }

export function checklistEditor(key: string, setup: boolean, hasPerm: (permission: string) => boolean): ConfigEditor | undefined {
  if (key === "order_account_fields") return "order-account-fields"
  if (key === "withdraw" && hasPerm("withdraw:config") && (setup || hasPerm("withdraw:view"))) return "withdraw"
  if (setup || hasPerm("order:view")) {
    if (key === "capacity") return "capacity"
    if (key === "grab_window") return "grab"
  }
}

export function quickConfigGroups(catalog: QuickCatalog | null, setup: boolean, hasPerm: (permission: string) => boolean) {
  const entries = [...(catalog?.legacy ?? []), ...(catalog?.policies ?? [])]
  const permitted = (id: string) => entries.some(entry => entry.id === id && entry.can_view === true && hasPerm(entry.permission))
  const groups: { title: string; entries: Shortcut[] }[] = []
  function group(title: string, items: (Shortcut | false)[]) {
    const visible = items.filter((item): item is Shortcut => item !== false)
    if (visible.length) groups.push({ title, entries: visible })
  }
  const categories = !setup && hasPerm("service:edit")
  const dispatch = !setup && permitted("order-controls")
  group("商品与下单", [
    categories && permitted("game-categories") && { title: "游戏类目", description: "维护服务商品关联的游戏。", to: "/service-items?config=games" },
    categories && permitted("service-categories") && { title: "服务分类", description: "维护商品分类及对应的订单类别。", to: "/service-items?config=categories" },
    !setup && !categories && (permitted("game-categories") || permitted("service-categories")) && { title: "服务商品", description: "查看游戏类目和服务分类下的商品。", to: "/service-items" },
    { title: "下单游戏资料", description: "按游戏或服务指定大区、昵称等必填资料。", editor: "order-account-fields" },
  ])
  group("派单与接单", [
    (setup || permitted("provider-capacity")) && { title: "陪玩接单名额", description: "设置每位陪玩同时进行的订单上限。", editor: "capacity" },
    (setup || permitted("grab-window")) && { title: "抢单窗口", description: "设置本店收集抢单申请的时长。", editor: "grab" },
    dispatch && { title: "待接单时限", description: "设置新订单等待接单的最长时间。", editor: "timeout" },
    dispatch && { title: "峰谷限流", description: "按时段调整派单与接单配额。", editor: "traffic" },
    !setup && permitted("provider-pass") && { title: "接单通行证", description: "维护通行证价格、时长和接单延迟。", to: "/provider-passes" },
    !setup && permitted("kook-dispatch") && { title: "KOOK 群派单", description: "配置机器人和接收派单的频道。", to: "/kook-dispatch" },
  ])
  const memberSettings = !setup && permitted("club-config")
  group("会员与陪玩", [
    !setup && permitted("boss-types") && { title: "老板分级", description: "维护老板分级及对应折扣。", to: "/boss-types" },
    !setup && permitted("escort-levels") && { title: "陪玩等级", description: "维护陪玩档位和适用的抽成规则。", to: "/escort-levels" },
    memberSettings
      ? { title: "会员与积分设置", description: "设置会员权益、积分抵扣和使用范围。", to: "/member-settings" }
      : { title: "积分规则", description: "查看本店积分抵扣和使用范围。", to: "/dictionaries?section=points" },
  ])
  group("资金与结算", [
    (setup ? hasPerm("deposit_rule:view") : permitted("deposit-rules")) && { title: "押金与结款规则", description: "核对押金目标、单抵比例和收益冻结时限。", to: "/deposit-rules" },
    (setup || permitted("withdraw")) && hasPerm("withdraw:config") && { title: "提现规则", description: "设置单笔最低额、余额门槛和扣除比例。", editor: "withdraw" },
    !setup && permitted("payment-fee") && { title: "微信支付费率", description: "查看或修订本店支付手续费率。", to: "/payment-fee" },
    !setup && permitted("commission") && hasPerm("report:audit") && { title: "订单默认抽成", description: "维护报单使用的默认分账规则。", editor: "commission" },
    !setup && { title: "游戏抽成草稿", description: "按游戏维护抽成草稿并核对服务覆盖。", editor: "game-commission" },
  ])
  group("薪资与经营", dictionaryTypes.map(item => ({ ...item, to: `/dictionaries?section=${item.code}` })))
  return groups
}
