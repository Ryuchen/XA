import { Store } from "lucide-react"
import { useCurrentStore } from "@xa/shared"

export function BossStoreSwitcher() {
  const store = useCurrentStore()
  return <div className="boss-store-trigger inline-flex h-11 min-w-0 items-center gap-2 px-2.5" aria-label={`当前老板端门店：${store.name}`}>
    <Store className="size-4 shrink-0 text-primary" />
    <span className="boss-store-name max-w-40 truncate text-xs font-medium" title={store.name}>{store.name}</span>
  </div>
}
