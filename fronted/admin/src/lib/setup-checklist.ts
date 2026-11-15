export type SetupItem = {
  key: string
  name: string
  status: string
  can_confirm?: boolean
}

export const setupStatusLabels: Record<string, string> = {
  MISSING: "未保存",
  INVALID: "配置无效",
  NEEDS_CONFIRMATION: "待核对",
  SAVED: "已保存",
  CONFIRMED: "已确认",
  RESTRICTED: "无查看权限",
}

export function canCheckSetupItem(item: SetupItem) {
  return item.can_confirm === true && ["SAVED", "NEEDS_CONFIRMATION"].includes(item.status)
}

export function nextSetupStep<T extends SetupItem>(
  items: T[],
  canConfigure: (item: T) => boolean,
  canConfirm: (item: T) => boolean,
) {
  for (const item of items) {
    if (["MISSING", "INVALID"].includes(item.status) && canConfigure(item)) {
      return { item, kind: "configure" as const }
    }
    if (canConfirm(item)) return { item, kind: "confirm" as const }
  }
}

export function setupStatusSummary(items: SetupItem[]) {
  const confirmed = items.filter(item => item.status === "CONFIRMED").length
  const saved = items.filter(item => item.status === "SAVED").length
  return `共 ${items.length} 项 · ${confirmed} 项已确认 · ${saved} 项已保存`
}
