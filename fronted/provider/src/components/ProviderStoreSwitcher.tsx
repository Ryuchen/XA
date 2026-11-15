import { Store } from "lucide-react"
import { useCurrentStore } from "@xa/shared"

export function ProviderStoreSwitcher() {
  const store = useCurrentStore()
  return <div className="provider-store-trigger inline-flex h-11 min-w-0 items-center gap-2 px-2.5" aria-label={`当前陪玩端门店：${store.name}`}>
    <Store className="size-4 shrink-0 text-primary" />
    <span className="provider-store-name max-w-40 truncate text-xs font-medium" title={store.name}>{store.name}</span>
  </div>
}
