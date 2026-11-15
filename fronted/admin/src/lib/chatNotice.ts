/** Delayed invalidations must not announce messages that were already read. */
export function isUnreadCustomerMessage(message: unknown): message is { id: number } {
  if (!message || typeof message !== "object") return false
  const item = message as { id?: unknown; is_from_support?: unknown; is_read?: unknown }
  return item.is_from_support === false && item.is_read === false &&
    typeof item.id === "number" && Number.isSafeInteger(item.id) && item.id > 0
}
