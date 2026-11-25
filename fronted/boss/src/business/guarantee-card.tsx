import {
  createApiClient,
  getSession,
  getStoreSlug,
  useCurrentStore,
  useSession,
} from "@xa/shared"
import { useEffect, useState } from "react"
import { AlertCircle, RefreshCw, ShieldCheck } from "lucide-react"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Skeleton } from "../components/ui/skeleton"
import {
  beginStoreGuaranteeLoad,
  completeStoreGuaranteeLoad,
  emptyStoreGuaranteeState,
  failStoreGuaranteeLoad,
  formatStoreGuaranteeAmount,
  requestPublicStoreGuarantee,
  storeGuaranteePresentation,
  storeGuaranteeScope,
  type StoreGuaranteeState,
} from "./guarantee"

const guaranteeApi = createApiClient("boss")

export function StoreGuaranteeCardView({
  state,
  storeName,
  onRefresh,
}: {
  state: StoreGuaranteeState
  storeName: string
  onRefresh: () => void
}) {
  const snapshot = state.snapshot?.scope === state.scope ? state.snapshot : null
  const data = snapshot?.data
  const presentation = data ? storeGuaranteePresentation(data) : null
  return (
    <section className="boss-panel boss-detail-section boss-guarantee-card">
      <h2><ShieldCheck size={16} />门店保障</h2>
      <div className="flex items-center justify-between gap-3">
        <Badge variant={presentation && !presentation.amountsUnknown && data?.status === "COVERED" ? "default" : "outline"}>
          {presentation?.label || "保障信息待核"}
        </Badge>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          aria-label="刷新门店保障"
          disabled={state.loading}
          onClick={onRefresh}
        >
          <RefreshCw size={14} className={state.loading ? "animate-spin" : ""} />
          {state.loading ? "读取中" : "刷新"}
        </Button>
      </div>
      <p className="mt-3 text-xs leading-5 text-muted-foreground">{storeName} · 本店保证金公示</p>
      {state.loading && !data && (
        <div className="mt-4 space-y-3" aria-busy="true" aria-label="正在读取门店保障">
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-16 w-full" />
          <p role="status" className="text-sm text-muted-foreground">正在读取当前门店保障，尚未采用任何金额。</p>
        </div>
      )}
      {!state.loading && !data && state.error && (
        <div className="boss-feedback error mt-4" role="alert">
          <AlertCircle size={18} />
          <div><strong>保障信息待核</strong><p>{state.error.message}</p><p>已约定、已收净额及待补登记金额：待核。</p></div>
        </div>
      )}
      {data && presentation && (
        <div
          className="mt-4"
          data-status={data.status}
          data-stale={state.stale ? "true" : "false"}
        >
          <p className="text-sm leading-6">{presentation.description}</p>
          <div className="mt-3">
            <dl className="boss-info">
              <div><dt>已约定保证金</dt><dd>{formatStoreGuaranteeAmount(data.amountDueFen)}</dd></div>
              <div><dt>已收净额（凭证登记）</dt><dd>{formatStoreGuaranteeAmount(data.netReceivedFen)}</dd></div>
              <div><dt>待补登记金额</dt><dd>{formatStoreGuaranteeAmount(data.amountRemainingFen)}</dd></div>
            </dl>
          </div>
          {presentation.amountsUnknown && <p role="alert" className="mt-3 text-xs leading-5 text-muted-foreground">未知金额不按 0 计算，也不由其他字段推断。</p>}
          <p className="mt-4 text-xs leading-5 text-muted-foreground">此处仅为本店合同与线下收退凭证登记口径，不代表银行到账或支付渠道确认，也不构成赔付承诺。</p>
        </div>
      )}
      {data && state.loading && <p role="status" className="mt-3 text-xs text-muted-foreground">正在刷新；当前暂为上次成功读取的快照，完成后才会更新。</p>}
      {data && state.stale && state.error && (
        <div className="boss-feedback error mt-4" role="alert">
          <AlertCircle size={18} />
          <div><strong>上次快照已过期</strong><p>刷新失败，以上{storeName}信息不能视为当前保障状态。{state.error.message}</p></div>
        </div>
      )}
    </section>
  )
}

export function StoreGuaranteeCard() {
  const store = useCurrentStore()
  const session = useSession("boss")
  const scope = storeGuaranteeScope(store, session.revision)
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState<StoreGuaranteeState>(() => emptyStoreGuaranteeState(scope))
  useEffect(() => {
    const controller = new AbortController()
    const sessionRevision = session.revision
    setState(previous => beginStoreGuaranteeLoad(previous, scope))
    const current = () => !controller.signal.aborted &&
      getStoreSlug() === store.slug &&
      getSession("boss").revision === sessionRevision
    void requestPublicStoreGuarantee(
      (path, query, options) => guaranteeApi.get(path, query, options),
      { store, resolvedSlug: getStoreSlug(), signal: controller.signal },
    ).then(payload => {
      if (current()) setState(previous => completeStoreGuaranteeLoad(previous, scope, payload))
    }).catch(cause => {
      if (current()) setState(previous => failStoreGuaranteeLoad(previous, scope, cause))
    })
    return () => controller.abort()
  }, [scope, attempt])
  const visible = state.scope === scope ? state : emptyStoreGuaranteeState(scope)
  return (
    <StoreGuaranteeCardView
      state={visible}
      storeName={store.name}
      onRefresh={() => setAttempt(value => value + 1)}
    />
  )
}
