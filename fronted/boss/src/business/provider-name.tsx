import { useCurrentStore } from "@xa/shared"
import { providerNameParts } from "./provider-name-format"
export { formatProviderName } from "./provider-name-format"

export function ProviderName({ value }: { value: unknown }) {
  const store = useCurrentStore()
  const parts = providerNameParts(value, store.provider_name_prefix)
  return <>{parts.prefix && <span className="boss-provider-prefix">{parts.prefix}-</span>}{parts.name}</>
}
