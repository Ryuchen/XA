import { storeBrandLogo, type StoreInfo } from "@xa/shared"

export function StoreLogo({ store, className = "size-10" }: { store: Pick<StoreInfo, "name" | "slug" | "logo_url">; className?: string }) {
  return <img
    data-store-logo={store.slug}
    src={storeBrandLogo(store, import.meta.env.BASE_URL)}
    alt=""
    width={40}
    height={40}
    className={`shrink-0 rounded-xl border bg-card object-contain p-1 shadow-sm ${className}`}
  />
}
