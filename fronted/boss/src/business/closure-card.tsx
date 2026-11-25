import {
  createApiClient,
  getSession,
  getStoreSlug,
  useCurrentStore,
  useSession,
} from "@xa/shared"
import { useEffect, useState } from "react"
import { AlertCircle, Megaphone, RefreshCw } from "lucide-react"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Skeleton } from "../components/ui/skeleton"
import {
  beginStoreClosureLoad,
  completeStoreClosureLoad,
  emptyStoreClosureState,
  failStoreClosureLoad,
  formatStoreClosureTime,
  requestPublicStoreClosure,
  storeClosurePresentation,
  storeClosureScope,
  type StoreClosureState,
} from "./closure"

const closureApi = createApiClient("boss")

export function StoreClosureCardView({
  state,
  storeName,
  onRefresh,
}: {
  state: StoreClosureState
  storeName: string
  onRefresh: () => void
}) {
  const snapshot = state.snapshot?.scope === state.scope ? state.snapshot : null
  const data = snapshot?.data
  const presentation = data ? storeClosurePresentation(data) : null
  return (
    <section className="boss-panel boss-detail-section boss-guarantee-card boss-closure-card">
      <h2><Megaphone size={16} />闭店公告</h2>
      <div className="flex items-center justify-between gap-3">
        <Badge variant={data?.status === "NOTICE_ACTIVE" ? "secondary" : "outline"}>
          {presentation?.label || "状态待核"}
        </Badge>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          aria-label="刷新闭店公告"
          disabled={state.loading}
          onClick={onRefresh}
        >
          <RefreshCw size={14} className={state.loading ? "animate-spin" : ""} />
          {state.loading ? "读取中" : "刷新"}
        </Button>
      </div>
      <p className="mt-3 text-xs leading-5 text-muted-foreground">{storeName} · 本店闭店公示</p>
      {state.loading && !data && (
        <div className="mt-4 space-y-3" aria-busy="true" aria-label="正在读取闭店公告">
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-16 w-full" />
          <p role="status" className="text-sm text-muted-foreground">正在读取当前门店闭店公告，尚未采用任何状态。</p>
        </div>
      )}
      {!state.loading && !data && state.error && (
        <div className="boss-feedback error mt-4" role="alert">
          <AlertCircle size={18} />
          <div><strong>闭店状态待核</strong><p>{state.error.message}</p><p>不能将读取失败解释为未公示。</p></div>
        </div>
      )}
      {data && presentation && (
        <div className="mt-4" data-status={data.status} data-stale={state.stale ? "true" : "false"}>
          <p className="text-sm leading-6" role={data.status === "UNKNOWN" || data.status === "DISABLED" ? "alert" : undefined}>
            {presentation.description}
          </p>
          {(data.status === "NOTICE_ACTIVE" || data.status === "SETTLEMENT_PENDING") && data.earliestSettlementAt && (
            <div className="mt-3">
              <dl className="boss-info">
                <div>
                  <dt>{data.status === "SETTLEMENT_PENDING" ? "原公示最早结清时间" : "最早结清时间"}</dt>
                  <dd>{formatStoreClosureTime(data.earliestSettlementAt)}</dd>
                </div>
              </dl>
            </div>
          )}
          {data.status === "NOTICE_ACTIVE" && <p className="mt-3 text-xs leading-5 text-muted-foreground">此时点直接来自后端公示记录；不计算本地倒计时，也不表示届时自动停用或完成退款。</p>}
          {data.status === "SETTLEMENT_PENDING" && <p className="mt-3 text-xs leading-5 text-muted-foreground">公示期届满仅表示进入待结清，不代表余额已返还或门店已最终停用。</p>}
        </div>
      )}
      {data && state.loading && <p role="status" className="mt-3 text-xs text-muted-foreground">正在刷新；当前暂为上次成功读取的闭店公告快照。</p>}
      {data && state.stale && state.error && (
        <div className="boss-feedback error mt-4" role="alert">
          <AlertCircle size={18} />
          <div><strong>上次快照已过期</strong><p>刷新失败，以上{storeName}闭店公告不能视为当前状态。{state.error.message}</p></div>
        </div>
      )}
    </section>
  )
}

export function StoreClosureCard() {
  const store = useCurrentStore()
  const session = useSession("boss")
  const scope = storeClosureScope(store, session.revision)
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState<StoreClosureState>(() => emptyStoreClosureState(scope))
  useEffect(() => {
    const controller = new AbortController()
    const sessionRevision = session.revision
    setState(previous => beginStoreClosureLoad(previous, scope))
    const current = () => !controller.signal.aborted &&
      getStoreSlug() === store.slug &&
      getSession("boss").revision === sessionRevision
    void requestPublicStoreClosure(
      (path, query, options) => closureApi.get(path, query, options),
      { store, resolvedSlug: getStoreSlug(), signal: controller.signal },
    ).then(payload => {
      if (current()) setState(previous => completeStoreClosureLoad(previous, scope, payload))
    }).catch(cause => {
      if (current()) setState(previous => failStoreClosureLoad(previous, scope, cause))
    })
    return () => controller.abort()
  }, [scope, attempt])
  const visible = state.scope === scope ? state : emptyStoreClosureState(scope)
  return (
    <StoreClosureCardView
      state={visible}
      storeName={store.name}
      onRefresh={() => setAttempt(value => value + 1)}
    />
  )
}
