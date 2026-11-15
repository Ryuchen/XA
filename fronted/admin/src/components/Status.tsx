import { CircleCheck, CircleHelp, CircleMinus, CirclePause, CircleX, Clock3, Info, LoaderCircle, Settings2, TriangleAlert } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { enumLabel } from "@/lib/labels"

type Tone = "success" | "warning" | "destructive" | "muted" | "primary"
export type StatusReadState = "loading" | "unread" | "error"

const labels: Record<string, string> = {
  PENDING: "待处理", GRABBED: "已接单", IN_SERVICE: "服务中", COMPLETED: "已完成",
  CANCELLED: "已取消", UNPAID: "未支付", PAID: "已支付", PARTIALLY_REFUNDED: "部分退款",
  REFUNDED: "已退款", APPROVED: "已通过", REJECTED: "已驳回", OPEN: "处理中",
  RESOLVED: "已结案", FULFILLING: "交付中", AVAILABLE: "空闲", BUSY: "忙碌",
  OFFLINE: "离线", SUCCESS: "成功", FAILED: "失败", ACTIVE: "已启用",
  ENABLED: "已启用", DISABLED: "已停用", UNCONFIGURED: "未配置",
  UNREAD: "未读取", CONNECTED: "已连接", DISCONNECTED: "未连接",
  true: "已启用", false: "未启用", pending: "待确认", confirmed: "已确认",
  converted: "已转单", rejected: "已拒绝", cancelled: "已取消",
}
const positive = new Set(["COMPLETED", "APPROVED", "PAID", "RESOLVED", "SUCCESS", "AVAILABLE", "ACTIVE", "ENABLED", "CONNECTED", "true", "confirmed"])
const negative = new Set(["REJECTED", "FAILED", "BANNED", "rejected"])
const muted = new Set(["CANCELLED", "REFUNDED", "EXPIRED", "OFFLINE", "DISABLED", "ARCHIVED", "false", "cancelled", "expired"])
const pending = new Set(["PENDING", "UNPAID", "OPEN", "BUSY", "WAITING", "REVIEW", "DISCONNECTED", "pending"])
const disabledLabels = new Set(["未启用", "已停用", "未开放", "待开放", "已关闭"])
const unconfiguredLabels = new Set(["未配置", "尚未配置", "未设置", "尚未设置"])

/** Business values and read failures are separate facts. Callers retain their own permissions and transitions. */
export function Status({ value, label, tone, readState }: {
  value?: string | boolean | null
  label?: string
  tone?: Tone
  readState?: StatusReadState
}) {
  const key = String(value)
  const text = label || labels[key] || (value == null || value === "" ? "未读取" : enumLabel(value))
  const state = readState || (key === "UNREAD" || text === "未读取" ? "unread" : undefined)
  let display = text
  let resolvedTone: Tone = tone || (positive.has(key) ? "success" : negative.has(key) ? "destructive" : muted.has(key) ? "muted" : pending.has(key) ? "warning" : "primary")
  let Icon = resolvedTone === "success" ? CircleCheck : resolvedTone === "destructive" ? CircleX : resolvedTone === "warning" ? Clock3 : resolvedTone === "muted" ? CircleMinus : Info
  let kind: string = resolvedTone
  if (unconfiguredLabels.has(text) || key === "UNCONFIGURED") {
    resolvedTone = "muted"
    Icon = Settings2
    kind = "unconfigured"
  } else if (text === "未连接" || key === "DISCONNECTED") {
    resolvedTone = tone || "warning"
    Icon = Clock3
    kind = "disconnected"
  } else if (disabledLabels.has(text) || (value === false && !label) || key === "DISABLED") {
    resolvedTone = tone || "muted"
    Icon = CirclePause
    kind = "disabled"
  }
  if (state) {
    display = state === "loading" ? "读取中" : state === "error" ? "接口异常" : "未读取"
    resolvedTone = state === "error" ? "destructive" : "muted"
    Icon = state === "loading" ? LoaderCircle : state === "error" ? TriangleAlert : CircleHelp
    kind = state
  }
  return <Badge variant="outline" className="admin-status" data-status-kind={kind} data-status-tone={resolvedTone}>
    <Icon aria-hidden="true" className={state === "loading" ? "motion-safe:animate-spin" : undefined} />
    <span>{display}</span>
  </Badge>
}
