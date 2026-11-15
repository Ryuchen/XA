export const accountFieldLabels = {
  game_region: "游戏大区",
  game_nickname: "游戏昵称",
  game_uid: "游戏 UID",
} as const

export type AccountField = keyof typeof accountFieldLabels

export function requiredAccountFields(service: any): AccountField[] | null {
  const rules = service?.account_fields
  if (!rules || !["game", "service", "default"].includes(rules.source) || !Array.isArray(rules.fields)) return null
  if (rules.fields.some((field: unknown) => typeof field !== "string" || !Object.prototype.hasOwnProperty.call(accountFieldLabels, field)) ||
      new Set(rules.fields).size !== rules.fields.length) return null
  return rules.fields as AccountField[]
}

export function validateAccountFields(service: any, values: Record<AccountField, string>) {
  const required = requiredAccountFields(service)
  if (!required) throw new Error("游戏资料要求无法确认，请刷新页面")
  const missing = required.filter((field) => !values[field].trim()).map((field) => accountFieldLabels[field])
  if (missing.length) throw new Error(`请填写${missing.join("、")}`)
}
