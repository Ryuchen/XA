import {
  getSession,
  getStoreSlug,
  isAdminEntry,
  refreshSession,
  sessionChangedError,
  validStoreSlug,
} from "@xa/shared"
import { OrderExportError, requestOrderExport } from "./order-export-request.ts"

export { OrderExportError } from "./order-export-request.ts"

export async function downloadOrderExport(
  filters: Record<string, unknown>,
  options: { signal?: AbortSignal } = {},
): Promise<{ blob: Blob; filename: string }> {
  const session = getSession("admin")
  const store = getStoreSlug()
  if (!session.user || !session.token) throw new OrderExportError("请先登录后台后导出订单", 401)
  if (isAdminEntry() && !validStoreSlug(store)) throw new OrderExportError("请先使用所属门店的后台账号登录", 401)
  return requestOrderExport(filters, {
    token: session.token,
    store,
    refresh: async () => (await refreshSession("admin")).trim(),
    assertActive: () => {
      if (getStoreSlug() !== store || getSession("admin").revision !== session.revision) throw sessionChangedError()
    },
  }, options)
}
