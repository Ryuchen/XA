import type { CSSProperties, ReactNode } from "react"
import { Link } from "react-router"
import {
  h5BrandingPayload,
  h5Foreground,
  h5OrderingBlocked,
  SAAS_CAPABILITIES,
  useCurrentStore,
} from "@xa/shared"
import { Button } from "@/components/ui/button"

export function useBossBrand() {
  const store = useCurrentStore()
  if (
    !store.capabilities?.includes(SAAS_CAPABILITIES.branding) ||
    !store.boss_h5?.branding
  )
    return null
  try {
    const branding = h5BrandingPayload(store.boss_h5.branding)
    return {
      ...branding,
      style: {
        "--primary": branding.primary_color,
        "--primary-foreground": h5Foreground(branding.primary_color),
        "--h5-brand-color": branding.primary_color,
      } as CSSProperties,
    }
  } catch {
    return null
  }
}
export function BossModuleNotice() {
  const store = useCurrentStore()
  if (
    !store.capabilities?.includes(SAAS_CAPABILITIES.modules) ||
    !h5OrderingBlocked(store.boss_h5?.entitlement)
  )
    return null
  return (
    <div className="xa-role-scope" role="status">
      <strong>自主下单暂未开放</strong>
      <span>
        既有订单与权益仍可查看。
        <Link className="underline underline-offset-4" to="/support">
          联系本店客服
        </Link>
      </span>
    </div>
  )
}
/** Only new-order entry points are gated. Historical fulfillment and money remain reachable. */
export function H5OrderGate({ children }: { children: ReactNode }) {
  const store = useCurrentStore()
  const integrated = store.capabilities?.includes(SAAS_CAPABILITIES.modules)
  if (!integrated) return children
  if (
    store.boss_h5?.entitlement &&
    !h5OrderingBlocked(store.boss_h5.entitlement)
  )
    return children
  return (
    <section className="boss-panel space-y-4">
      <h1 className="text-xl font-semibold">自主下单暂未开放</h1>
      <p className="text-sm leading-7 text-muted-foreground">
        请联系{store.name}客服安排服务。你的既有订单、售后与权益仍可查看。
      </p>
      <div className="flex flex-wrap gap-3">
        <Button asChild>
          <Link to="/support">联系客服</Link>
        </Button>
        <Button variant="outline" asChild>
          <Link to="/orders">查看我的订单</Link>
        </Button>
      </div>
    </section>
  )
}
