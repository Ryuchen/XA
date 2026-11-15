import { formatMoney, formatDateTime, toRawAmount } from "@xa/shared"
export const amountToXaCoin = formatMoney
export const xaCoinToAmount = toRawAmount
export { formatDateTime }
export const numberText = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("zh-CN")
    : "—"
export const identity = (value: any) =>
  value?.nickname ||
  value?.username ||
  value?.name ||
  (value?.id ? `#${value.id}` : "—")
export const providerNames = (order: any) =>
  (order.providers || order.order_providers || [])
    .map(
      (person: any) =>
        person.nickname ||
        person.provider_name ||
        person.provider_nickname ||
        person.provider?.nickname ||
        person.provider?.username
    )
    .filter(Boolean)
    .join(" / ") ||
  order.provider_name ||
  order.provider_nickname ||
  "待安排"
