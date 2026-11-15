// Socket messages invalidate only the current business region. REST remains
// authoritative; heartbeats and unrelated events never reload page data.
import { operationPages } from "./operation-navigation.ts"
const order = ["order_status_update"]
const money = ["wallet_update", "withdraw_status_update", "report_status_update", ...order]
const chat = ["chat_message", "chat_session_update"]
const dashboard = [...money, ...chat, "reservation_status_update", "evaluation_status_update", "claim_queue_update"]
const pageEvents: Record<string, readonly string[]> = {
  dashboard,
  "player-dashboard": money,
  orders: order,
  reservations: ["reservation_status_update", ...order],
  reports: ["report_status_update", ...order],
  withdrawals: ["withdraw_status_update", "wallet_update"],
  wallets: money,
  transactions: money,
  "recharge-records": ["wallet_update"],
  "dispose-records": money,
  evaluations: ["evaluation_status_update"],
  escorts: [...order, "wallet_update", "provider_tags_update"],
  "provider-registrations": [],
  "provider-passes": ["wallet_update"],
  users: ["wallet_update"],
  chat,
  messages: ["site_message_update"],
  coupons: ["coupon_claim_update", "coupon_catalog_update"],
  checkin: ["checkin_progress_update", "checkin_config_update"],
  "operating-roi": money,
}
const operationEvents: Record<string, readonly string[]> = {
  claims: ["claim_queue_update"],
  rewards: ["reward_catalog_update", "claim_queue_update"],
  feed: ["club_feed_update"],
  namings: ["provider_tags_update", "claim_queue_update"],
  tiers: ["vip_config_update"],
}

export function adminRealtimeTypes(pathname: string, search = ""): readonly string[] {
  const page = pathname.replace(/^\//, "").split("/")[0]
  if (page === "club-operations")
    return operationEvents[new URLSearchParams(search).get("tab") || "claims"] || []
  const operation = operationPages.find(operation => operation.path === page)
  if (operation) return operationEvents[operation.section] || []
  return pageEvents[page] || []
}

export function isAdminRealtimeInvalidation(type: string, types: readonly string[]) {
  return type === "reconnected" || types.includes(type)
}
