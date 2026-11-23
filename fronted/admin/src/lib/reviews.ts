import { getSession, getStoreSlug } from "@xa/shared"

export interface PendingReview {
  key: string
  description: string
  path: string
  startedAt: string
  requestId?: string
}
const prefix = `xa:store:${getStoreSlug()}:xa:fronted:admin:review:`
const active = new Set<string>()
const changed = () => window.dispatchEvent(new Event("xa:admin-reviews"))
function storageKey(key: string) {
  const user = getSession("admin").user
  if (!user) throw new Error("请先登录。")
  return `${prefix}${user.id}:${key}`
}
export function pendingReviews(): PendingReview[] {
  const user = getSession("admin").user
  if (!user) return []
  const start = `${prefix}${user.id}:`
  const values: PendingReview[] = []
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i)
      if (key?.startsWith(start)) {
        try {
          const value = JSON.parse(localStorage.getItem(key) || "null")
          if (value?.key && value?.description) values.push(value)
        } catch {}
      }
    }
  } catch {}
  return values.sort((a, b) => b.startedAt.localeCompare(a.startedAt))
}
export function reviewActive(key: string) {
  try {
    return active.has(storageKey(key))
  } catch {
    return false
  }
}
export function clearReview(key: string, requestId?: string) {
  const storage = storageKey(key)
  if (requestId !== undefined) {
    try {
      const current = JSON.parse(localStorage.getItem(storage) || "null")
      if (current?.requestId !== requestId) return
    } catch { return }
  }
  localStorage.removeItem(storage)
  changed()
}
export function reviewDescriptor(
  path: string,
  method = "POST"
): { description: string; path: string } | null {
  if (/\/orders\/dispatch\/$/.test(path))
    return { description: "快捷派单扣款与创建订单", path: "/orders" }
  const order = path.match(
    /\/orders\/(\d+)\/(refund|negotiated-refund|subsidy-negotiation-confirm|transfer|assign|start|complete|cancel|enable-team-evidence)\/$/
  )
  if (order)
    return {
      description: `订单 #${order[1]} ${{ refund: "退款", "negotiated-refund": "协商核账退款", "subsidy-negotiation-confirm": "报酬协商终止", transfer: "转单与收益分配", assign: "指派陪玩", start: "开始服务", complete: "完成服务", cancel: "取消订单", "enable-team-evidence": "启用双方凭证" }[order[2] as "refund"] || "订单处理"}`,
      path: `/orders?detail=${order[1]}`,
    }
  const aftersale = path.match(/\/aftersales\/(\d+)\/refund\/$/)
  if (aftersale)
    return { description: `售后 #${aftersale[1]} 退款并结案`, path: "/orders" }
  const negotiation = path.match(/\/aftersales\/(\d+)\/negotiation-execution\/$/)
  if (negotiation)
    return { description: `售后 #${negotiation[1]} 协商退款`, path: "/orders" }
  const report = path.match(/\/reports\/(\d+)\/(approve|reject)\/$/)
  if (report)
    return {
      description: `报单 #${report[1]} ${report[2] === "approve" ? "审核通过" : "驳回"}`,
      path: `/reports?keyword=${report[1]}`,
    }
  const withdraw = path.match(
    /\/withdrawals\/(\d+)\/(claim|approve|reject|release-claim)\/$/
  )
  if (withdraw)
    return {
      description: `提现 #${withdraw[1]} 领取或审核处理`,
      path: `/withdrawals?keyword=${withdraw[1]}`,
    }
  const wallet = path.match(
    /\/wallets\/(?:(\d+)\/adjust|recharge|platform-topup)\/$/
  )
  if (wallet)
    return {
      description: wallet[1]
        ? `钱包 #${wallet[1]} 调账`
        : path.includes("platform-topup")
          ? "平台线下补入"
          : "客服充值入账",
      path: wallet[1]
        ? `/transactions?wallet_id=${wallet[1]}`
        : "/recharge-records",
    }
  if (/\/escorts\/\d+\/(dispose|deposit-refund)\/$/.test(path))
    return { description: "陪玩奖惩或退押金", path: "/dispose-records" }
  if (/\/orders\/reservations\/\d+\/convert\/$/.test(path))
    return { description: "预约扣款并转为履约订单", path: "/reservations" }
  if (method === "POST" && /\/orders\/reservations\/$/.test(path))
    return { description: "代老板创建预约", path: "/reservations" }
  const binding = path.match(
    /\/audition-links\/(\d+)\/(bind-boss|bind-provider)\/$/
  )
  if (binding)
    return {
      description: `试音招募 #${binding[1]} ${binding[2] === "bind-boss" ? "老板" : "陪玩"}入口绑定`,
      path: "/audition-links",
    }
  const auditionReview = path.match(
    /\/audition-signups\/(\d+)\/(approve|reject)\/$/
  )
  if (auditionReview)
    return {
      description: `试音报名 #${auditionReview[1]} ${auditionReview[2] === "approve" ? "审核通过" : "驳回"}`,
      path: "/audition-signups",
    }
  const claim = path.match(
    /\/club-claims\/(\d+)\/(process|review-tag|suspend-tag)\/$/
  )
  if (claim)
    return {
      description: `权益 #${claim[1]} 交付或标签处理`,
      path: "/club-claims",
    }
  const registration = path.match(
    /\/provider-registrations\/(\d+)\/(approve|reject)\/$/
  )
  if (registration)
    return {
      description: `陪玩注册 #${registration[1]} ${registration[2] === "approve" ? "审核开通" : "退回补充"}`,
      path: "/provider-registrations",
    }
  const config = path.match(/\/config\/([^/]+)\/?$/)
  if (config && method === "PUT")
    return {
      description: `${({ commission: "默认抽成", withdraw: "提现规则", "deposit-rules": "押金规则", "provider-pass": "通行证", checkin: "签到规则", "order-timeout": "待接单时限", "payment-fee": "支付费率", "kook-dispatch": "群派单配置" } as Record<string, string>)[config[1]] || "后台配置"}保存`,
      path:
        (
          {
            commission: "/reports",
            withdraw: "/withdrawals",
            "deposit-rules": "/deposit-rules",
            "provider-pass": "/provider-passes",
            checkin: "/checkin",
            "order-timeout": "/orders",
            "payment-fee": "/payment-fee",
            "kook-dispatch": "/kook-dispatch",
          } as Record<string, string>
        )[config[1]] || "/dashboard",
    }
  const lotteryConfiguration = path.match(/\/lotteries\/(\d+)\/configure\/$/)
  if (lotteryConfiguration)
    return {
      description: `转盘活动 #${lotteryConfiguration[1]} 奖池配置`,
      path: "/lotteries",
    }
  const resources: Record<string, [string, string]> = {
    "service-items": ["服务套餐", "/service-items"],
    "game-categories": ["游戏类目", "/service-items"],
    "service-categories": ["服务分类", "/service-items"],
    gameplays: ["玩法", "/gameplays"],
    "boss-types": ["老板分级", "/boss-types"],
    "escort-levels": ["陪玩等级", "/escort-levels"],
    users: ["老板资料", "/users"],
    escorts: ["陪玩档案", "/escorts"],
    promotions: ["促销活动", "/promotions"],
    coupons: ["优惠券", "/coupons"],
    banners: ["轮播图", "/banners"],
    announcements: ["公告", "/announcements"],
    "support-cards": ["客服名片", "/support-cards"],
    achievements: ["成就规则", "/achievements"],
    "checkin-gifts": ["签到礼物", "/checkin"],
    admins: ["后台成员", "/admins"],
    roles: ["角色权限", "/roles"],
    "club-rewards": ["积分礼品", "/club-rewards"],
    "club-content": ["运营内容", "/club-content"],
    "recharge-packages": ["充值档位", "/recharge-packages"],
    "gift-benefits": ["赠礼附送权益", "/gift-benefits"],
    "broadcast-rules": ["播报规则", "/broadcast-rules"],
    broadcasts: ["播报展示", "/broadcasts"],
    "provider-namings": ["陪玩冠名", "/provider-namings"],
    "vip-tiers": ["VIP 权益", "/vip-tiers"],
    lotteries: ["转盘活动", "/lotteries"],
    "audition-links": ["试音招募", "/audition-links"],
    "external-points": ["外部积分登记", "/external-points"],
    "club-config": ["运营设置", "/member-settings"],
    "dictionary-items": ["业务字典", "/dictionaries"],
  }
  const resource = path.match(/^\/admin\/([a-z-]+)\/(\d+\/)?$/)
  if (resource && resources[resource[1]]) {
    const [name, url] = resources[resource[1]]
    return {
      description: `${name}${resource[2] ? ` #${resource[2].slice(0, -1)}` : ""} ${method === "DELETE" ? "删除" : method === "POST" ? "创建" : "保存"}`,
      path: url,
    }
  }
  return null
}
export async function trackReview<T>(
  key: string,
  descriptor: { description: string; path: string },
  requestId: string | undefined,
  run: () => Promise<T>
): Promise<T> {
  const storage = storageKey(key)
  if (localStorage.getItem(storage))
    throw new Error(
      "同一项操作有一笔结果待核对，请先使用页面上方的处理记录核对并解除限制。"
    )
  const saved = JSON.stringify({ key, ...descriptor, startedAt: new Date().toISOString(), requestId } satisfies PendingReview)
  localStorage.setItem(storage, saved)
  function clearOwnReview() {
    if (localStorage.getItem(storage) === saved) localStorage.removeItem(storage)
  }
  active.add(storage)
  changed()
  try {
    const result = await run()
    clearOwnReview()
    return result
  } catch (cause: any) {
    const status = cause.status >= 400 ? cause.status : cause.code
    if (
      Number.isInteger(status) &&
      status >= 400 &&
      status < 500 &&
      ![408, 409, 429].includes(status)
    )
      clearOwnReview()
    throw cause
  } finally {
    active.delete(storage)
    changed()
  }
}
