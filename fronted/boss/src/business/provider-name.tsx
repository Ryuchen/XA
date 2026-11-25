import { useCurrentStore } from "@xa/shared"
import { providerNameParts } from "./provider-name-format"
export { formatProviderName } from "./provider-name-format"

export function ProviderNameLabel({ value, prefix }: { value: unknown; prefix?: unknown }) {
  const parts = providerNameParts(value, prefix)
  return <>{parts.prefix && <span className="boss-provider-prefix">{parts.prefix}-</span>}{parts.name}</>
}

export function ProviderName({ value }: { value: unknown }) {
  const store = useCurrentStore()
  return <ProviderNameLabel value={value} prefix={store.provider_name_prefix} />
}
