/** Presentation only; the supplied business nickname is never modified. */
export function providerNameParts(value: unknown, prefixValue: unknown) {
  const name = typeof value === "string" ? value.trim() : ""
  const prefix = typeof prefixValue === "string" ? prefixValue.trim().toUpperCase() : ""
  if (!name) return { prefix: "", name: "" }
  if (!/^[A-Z]{2,8}$/.test(prefix)) return { prefix: "", name }
  let displayName = name
  while (displayName.slice(0, prefix.length + 1).toUpperCase() === `${prefix}-`) {
    displayName = displayName.slice(prefix.length + 1)
  }
  return { prefix, name: displayName }
}

export function formatProviderName(value: unknown, prefix: unknown) {
  const parts = providerNameParts(value, prefix)
  return parts.prefix ? `${parts.prefix}-${parts.name}` : parts.name
}
