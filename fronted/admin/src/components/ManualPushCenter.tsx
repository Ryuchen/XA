import { useEffect, useMemo, useState } from "react"
import { toast } from "sonner"
import { useCurrentStore } from "@xa/shared"
import { request, requestId } from "@/api/request"
import { useAdminAuth } from "@/lib/auth"
import { formatDateTime } from "@/lib/format"
import {
  createManualPushIntent,
  findManualPushRecovery,
  manualPushErrorMessage,
  manualPushReadMode,
  manualPushWriteFailure,
  manualPushWriteMessage,
  parseManualPushCampaignPage,
  parseManualPushIntent,
  parseManualPushResult,
  parseManualPushRetryResult,
  parseManualPushStatusPage,
  submitManualPushIntent,
  updateManualRetrySelection,
  type ManualPushAudience,
  type ManualPushCampaign,
  type ManualPushCampaignDetail,
  type ManualPushCampaignFilter,
  type ManualPushIntent,
  type ManualPushRecipient,
  type ManualPushRecipientFilter,
  type ManualPushResult,
  type ManualPushType,
} from "@/lib/manual-push"
import { Feedback, Field, FormSection, MutationDialog, OptionPicker, Pagination, ReadOnlyNotice, useLoad } from "@/components/business"
import { ReadErrorNotice } from "@/components/page-data-state"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { RefreshCw, Send } from "@/components/ui/icons"

const typeLabels: Record<ManualPushType, string> = {
  SYSTEM: "系统通知",
  ORDER: "订单消息",
  SUPPORT: "客服消息",
  PROMOTION: "活动通知",
}
const audienceLabels: Record<ManualPushAudience, string> = {
  CUSTOM: "所选老板 / 陪玩账号",
  BOSS: "全体老板",
  PROVIDER: "全体陪玩",
  STAFF: "全体员工",
  ALL: "全部业务账号（含员工）",
}

async function fetchCampaigns(query: {
  page: number
  pageSize: number
  status: "" | ManualPushCampaignFilter
  creatorId?: number
  clientRequestId?: string
}) {
  const result = await request<unknown>({
    url: "/messages/push-campaigns/",
    params: {
      page: query.page,
      page_size: query.pageSize,
      status: query.status,
      creator_id: query.creatorId,
      client_request_id: query.clientRequestId,
    },
  })
  return parseManualPushCampaignPage(result.data, query)
}
async function fetchCampaignStatus(query: {
  campaignId: number
  page: number
  pageSize: number
  status: "" | ManualPushRecipientFilter
}) {
  const result = await request<unknown>({
    url: "/messages/push-status/",
    params: { campaign_id: query.campaignId, page: query.page, page_size: query.pageSize, status: query.status },
  })
  return parseManualPushStatusPage(result.data, query)
}
async function postManualPush(intent: ManualPushIntent) {
  const result = await request<unknown>({ url: "/messages/push/", method: "POST", data: intent.payload, reviewPath: "/messages" })
  return parseManualPushResult(result.data, intent)
}
async function postManualPushRetry(campaign: ManualPushCampaignDetail, recipientIds: number[]) {
  const result = await request<unknown>({
    url: "/messages/push-retry/",
    method: "POST",
    data: { campaign_id: campaign.campaign_id, recipient_ids: recipientIds },
    reviewPath: "/messages",
  })
  return parseManualPushRetryResult(result.data, { campaignId: campaign.campaign_id, clientRequestId: campaign.client_request_id })
}

export default function ManualPushCenter({ canPush, onMessagesChanged }: { canPush: boolean; onMessagesChanged: () => void }) {
  const auth = useAdminAuth()
  const store = useCurrentStore()
  const creatorId = Number(auth.profile?.id)
  const context = useMemo(() => ({ storeSlug: store.slug, creatorId }), [store.slug, creatorId])
  const storageKey = `xa:store:${store.slug}:xa:fronted:admin:manual-push:${creatorId}`
  const restored = useMemo(() => {
    try {
      if (typeof sessionStorage === "undefined") return { intent: null, error: "" }
      return { intent: parseManualPushIntent(sessionStorage.getItem(storageKey), context), error: "" }
    } catch (cause) {
      return { intent: null, error: manualPushErrorMessage(cause) }
    }
  }, [storageKey, context])
  const [pending, setPending] = useState<ManualPushIntent | null>(restored.intent)
  const [recoveryError, setRecoveryError] = useState(restored.error)
  const [recoveryNotFound, setRecoveryNotFound] = useState(false)
  const [creating, setCreating] = useState(false)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState("")
  const [pushDenied, setPushDenied] = useState(false)
  const [readDenied, setReadDenied] = useState(false)
  const [status, setStatus] = useState<"" | ManualPushCampaignFilter>("")
  const [draftRequestId, setDraftRequestId] = useState("")
  const [requestIdFilter, setRequestIdFilter] = useState("")
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [detailId, setDetailId] = useState<number | null>(null)

  useEffect(() => {
    setPushDenied(false)
    setReadDenied(false)
  }, [auth.revision, store.slug, creatorId])
  useEffect(() => setPage(1), [status, requestIdFilter, pageSize])

  const campaigns = useLoad(
    () => fetchCampaigns({
      page,
      pageSize,
      status,
      clientRequestId: requestIdFilter || undefined,
    }).catch(cause => {
      if ((cause as { status?: number })?.status === 403) setReadDenied(true)
      throw cause
    }),
    [store.slug, page, pageSize, status, requestIdFilter],
  )
  const canWrite = canPush && !pushDenied && !readDenied && creatorId > 0
  const mode = manualPushReadMode({
    hasSnapshot: !!campaigns.data,
    loading: campaigns.loading,
    error: campaigns.error,
    itemCount: campaigns.data?.list.length,
  })

  function saveIntent(intent: ManualPushIntent) {
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(intent))
    } catch {
      throw Object.assign(new Error("浏览器无法保存原请求，本次未发送；请允许会话存储后重试"), { code: "MANUAL_PUSH_STORAGE_UNAVAILABLE" })
    }
    setPending(intent)
    setRecoveryError("")
    setRecoveryNotFound(false)
  }
  function clearIntent() {
    sessionStorage.removeItem(storageKey)
    setPending(null)
    setRecoveryNotFound(false)
  }
  async function refreshAfterWrite() {
    await campaigns.reload()
    onMessagesChanged()
  }
  async function runIntent(intent: ManualPushIntent, recovering = false): Promise<ManualPushResult> {
    if (busy || !canWrite) throw new Error("当前权限或请求状态不允许发送站内群发")
    setBusy(true)
    setActionError("")
    try {
      const result = await submitManualPushIntent(intent, { save: saveIntent, send: postManualPush, clear: clearIntent }, { recovering })
      setDetailId(result.campaign_id)
      toast.success(`批次 #${result.campaign_id}：已创建 ${result.count} 条站内记录，待处理 ${result.pending_count} 条；不代表外部渠道已送达`)
      await refreshAfterWrite()
      return result
    } catch (cause) {
      if ((cause as { status?: number })?.status === 403) setPushDenied(true)
      const code = (cause as { code?: string })?.code
      setActionError(code === "MANUAL_PUSH_STORAGE_UNAVAILABLE" ? manualPushErrorMessage(cause) : manualPushWriteMessage(cause, recovering))
      if ((cause as { status?: number })?.status === 409) await campaigns.reload()
      throw cause
    } finally {
      setBusy(false)
    }
  }
  async function recoverByRequestId() {
    if (!pending || busy || readDenied) return
    setBusy(true)
    setActionError("")
    try {
      const recovered = await findManualPushRecovery(pending, {
        lookup: intent => fetchCampaigns({
          page: 1,
          pageSize: 10,
          status: "",
          creatorId: intent.creatorId,
          clientRequestId: intent.payload.client_request_id,
        }),
        detail: campaignId => fetchCampaignStatus({ campaignId, page: 1, pageSize: 1, status: "" }),
      })
      if (recovered.status === "NOT_FOUND") {
        setRecoveryNotFound(true)
        setActionError("服务端暂未查到原请求批次。原请求可能未到达；如确认需要继续，只能使用原请求号和完整内容重试。")
        return
      }
      clearIntent()
      setDetailId(recovered.campaign.campaign_id)
      toast.success(`已按原请求号找回批次 #${recovered.campaign.campaign_id}，未重复发送`)
      await refreshAfterWrite()
    } catch (cause) {
      if ((cause as { status?: number })?.status === 403) setReadDenied(true)
      setActionError(`${manualPushErrorMessage(cause)} 原请求仍保留，不能换号重发。`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="space-y-4 rounded-xl border bg-card p-5" aria-labelledby="manual-push-title" data-manual-push-center>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="manual-push-title" className="text-lg font-semibold">站内群发批次</h2>
          <p className="mt-1 max-w-3xl text-xs leading-5 text-muted-foreground">
            CREATED 仅表示站内消息记录已创建，不代表 WebSocket 或其他外部渠道已送达。失败数是待处理记录的子集，不与待处理数相加。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button type="button" size="sm" variant="outline" onClick={() => void campaigns.reload()} disabled={campaigns.loading || busy}>
            <RefreshCw className={campaigns.loading ? "animate-spin" : ""} />刷新批次
          </Button>
          {canWrite && <Button type="button" size="sm" onClick={() => setCreating(true)} disabled={!!pending || !!recoveryError || busy}><Send />新建站内群发</Button>}
        </div>
      </div>
      {!canPush && <ReadOnlyNotice canEdit={false} subject="群发批次和逐人处理结果" />}
      {(pushDenied || readDenied) && <Feedback message="服务端已拒绝当前权限，群发与重试操作已停用。请等待权限同步或重新进入后台；不会根据旧页面状态继续写入。" />}
      {recoveryError && <Feedback message={recoveryError} />}
      {pending && (
        <div role="alert" className="space-y-3 rounded-xl border border-amber-500/50 bg-amber-500/5 p-4" data-manual-push-pending>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h3 className="font-semibold">有一笔站内群发结果未知</h3>
              <p className="mt-1 text-sm text-muted-foreground">{pending.payload.title} · {audienceLabels[pending.payload.audience]}</p>
              <p className="mt-2 break-all font-mono text-xs">请求编号：{pending.payload.client_request_id}</p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button type="button" variant="outline" onClick={() => void recoverByRequestId()} disabled={busy || readDenied}>按原号查询批次</Button>
              {recoveryNotFound && <Button type="button" onClick={() => void runIntent(pending, true)} disabled={busy || !canWrite}>原号原内容重试</Button>}
            </div>
          </div>
          <p className="text-xs text-muted-foreground">恢复查询不会创建新批次；只有查不到原批次后，才显示原号重试入口。</p>
        </div>
      )}
      <Feedback message={actionError} />

      <div className="flex flex-wrap items-end gap-3 rounded-lg bg-muted/20 p-3">
        <Field
          field={{
            key: "manual-push-status",
            label: "批次状态",
            type: "select",
            options: [["PENDING", "有待处理记录"], ["FAILED", "有失败记录"], ["SKIPPED", "有跳过记录"], ["COMPLETED", "无待处理记录"]],
          }}
          value={status}
          onChange={value => setStatus(value as "" | ManualPushCampaignFilter)}
        />
        <form className="flex flex-wrap items-end gap-2" onSubmit={event => {
          event.preventDefault()
          if (draftRequestId !== draftRequestId.trim()) {
            setActionError("请求编号前后不能有空格")
            return
          }
          setActionError("")
          setRequestIdFilter(draftRequestId)
        }}>
          <label className="space-y-2 text-sm"><span>精确请求编号</span><Input className="w-72" value={draftRequestId} onChange={event => setDraftRequestId(event.target.value)} placeholder="按原 client_request_id 查询" /></label>
          <Button type="submit" size="sm" variant="outline">查询</Button>
          <Button type="button" size="sm" variant="ghost" disabled={!status && !draftRequestId && !requestIdFilter} onClick={() => { setStatus(""); setDraftRequestId(""); setRequestIdFilter("") }}>清除筛选</Button>
        </form>
      </div>

      <ReadErrorNotice error={campaigns.error} retry={() => void campaigns.reload()} hasSnapshot={!!campaigns.data} />
      {mode === "stale" && <p role="status" className="rounded-lg border border-amber-500/40 bg-amber-500/5 px-4 py-3 text-sm">当前批次列表为旧快照，状态和统计可能已经过期；请以重新读取结果为准。</p>}
      {mode === "loading" ? <p role="status" className="rounded-lg bg-muted/20 p-6 text-sm text-muted-foreground">正在读取群发批次和统计…</p>
        : mode === "error" ? <div className="rounded-lg bg-muted/20 p-6 text-sm text-muted-foreground">批次尚未读取。请核对 message:view 权限和网络后重试，未知统计不会显示为零。</div>
        : mode === "empty" ? <p role="status" className="rounded-lg bg-muted/20 p-6 text-center text-sm text-muted-foreground">{requestIdFilter || status ? "当前筛选没有群发批次。" : "暂无站内群发批次。"}</p>
        : campaigns.data ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1080px] text-sm">
              <caption className="sr-only">站内群发批次，统计失败数为待处理子集</caption>
              <thead><tr className="border-b text-left text-xs text-muted-foreground"><th className="py-3 pr-4 font-medium">批次 / 标题</th><th className="py-3 pr-4 font-medium">接收范围</th><th className="py-3 pr-4 font-medium">接收人总数</th><th className="py-3 pr-4 font-medium">站内已创建</th><th className="py-3 pr-4 font-medium">待处理</th><th className="py-3 pr-4 font-medium">其中失败</th><th className="py-3 pr-4 font-medium">已跳过</th><th className="py-3 pr-4 font-medium">创建时间</th><th className="py-3 text-right font-medium">操作</th></tr></thead>
              <tbody>{campaigns.data.list.map(campaign => (
                <tr key={campaign.campaign_id} className="border-b last:border-0">
                  <td className="py-3 pr-4"><button type="button" className="text-left font-medium text-primary hover:underline" onClick={() => setDetailId(campaign.campaign_id)}>#{campaign.campaign_id} · {campaign.title}</button><p className="mt-1 max-w-56 truncate font-mono text-xs text-muted-foreground" title={campaign.client_request_id}>{campaign.client_request_id}</p></td>
                  <td className="py-3 pr-4">{selectorLabel(campaign)}</td><td className="py-3 pr-4 tabular-nums">{campaign.total}</td><td className="py-3 pr-4 tabular-nums">{campaign.count}</td><td className="py-3 pr-4 tabular-nums">{campaign.pending_count}</td><td className="py-3 pr-4 tabular-nums">{campaign.failed_count}</td><td className="py-3 pr-4 tabular-nums">{campaign.skipped_count}</td><td className="py-3 pr-4">{formatDateTime(campaign.created_at)}</td><td className="py-3 text-right"><Button type="button" size="sm" variant="outline" onClick={() => setDetailId(campaign.campaign_id)}>逐人状态</Button></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        ) : null}
      <Pagination
        snapshotAvailable={!!campaigns.data}
        page={campaigns.data?.page || page}
        total={campaigns.data?.total || 0}
        pageSize={campaigns.data?.page_size || pageSize}
        loading={campaigns.loading}
        onChange={setPage}
        onPageSizeChange={setPageSize}
      />

      {detailId !== null && (
        <ManualPushCampaignDetailView
          key={`${store.slug}:${detailId}`}
          campaignId={detailId}
          canWrite={canWrite}
          onClose={() => setDetailId(null)}
          onChanged={refreshAfterWrite}
          onPushDenied={() => setPushDenied(true)}
          onReadDenied={() => setReadDenied(true)}
        />
      )}
      {creating && canWrite && (
        <ManualPushComposer
          context={context}
          canWrite={canWrite}
          onClose={() => setCreating(false)}
          onSubmit={runIntent}
        />
      )}
    </section>
  )
}

function selectorLabel(campaign: ManualPushCampaign) {
  if ("broadcast" in campaign.selector) return audienceLabels.ALL
  if ("audience" in campaign.selector) return audienceLabels[campaign.selector.audience]
  return `指定账号 ${campaign.selector.accounts.length + campaign.selector.users.length} 个`
}

function ManualPushComposer({ context, canWrite, onClose, onSubmit }: {
  context: { storeSlug: string; creatorId: number }
  canWrite: boolean
  onClose: () => void
  onSubmit: (intent: ManualPushIntent) => Promise<ManualPushResult>
}) {
  const [form, setForm] = useState<{
    audience: ManualPushAudience
    recipientAccountIds: number[]
    type: ManualPushType
    title: string
    preview: string
    detail: string
  }>({ audience: "CUSTOM", recipientAccountIds: [], type: "SYSTEM", title: "", preview: "", detail: "" })
  const [reviewed, setReviewed] = useState(false)
  useEffect(() => { if (!canWrite) onClose() }, [canWrite, onClose])
  function change<K extends keyof typeof form>(key: K, value: (typeof form)[K]) {
    setForm(old => ({ ...old, [key]: value }))
    setReviewed(false)
  }
  return (
    <MutationDialog
      contentClassName="sm:max-w-5xl"
      title="新建站内群发"
      description="请求号和完整内容会在首次提交前保存。CREATED 只代表站内记录已创建，不代表外部渠道送达。"
      onClose={onClose}
      submitText="确认创建群发批次"
      confirmDiscard
      readOnly={!canWrite}
      disabled={!canWrite || !reviewed || !form.title.trim() || form.title.trim().length > 100 || form.preview.trim().length > 255 || (form.audience === "CUSTOM" && !form.recipientAccountIds.length)}
      onSubmit={async () => {
        const intent = createManualPushIntent(context, form, requestId("manual-push"))
        try {
          await onSubmit(intent)
        } catch (cause) {
          const failure = manualPushWriteFailure(cause)
          const storageFailure = (cause as { code?: string })?.code === "MANUAL_PUSH_STORAGE_UNAVAILABLE"
          if (!storageFailure && (failure.kind === "unknown" || failure.kind === "denied" || failure.kind === "conflict")) onClose()
          throw cause
        }
      }}
    >
      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1.4fr)_320px]">
        <FormSection title="接收范围与消息内容">
          <div className="grid grid-cols-2 gap-4">
            <Field field={{ key: "manual-push-audience", label: "接收范围", type: "select", options: Object.entries(audienceLabels) }} value={form.audience} onChange={value => { change("audience", value as ManualPushAudience); if (value !== "CUSTOM") change("recipientAccountIds", []) }} />
            <Field field={{ key: "manual-push-type", label: "消息类型", type: "select", options: Object.entries(typeLabels) }} value={form.type} onChange={value => change("type", value as ManualPushType)} />
            {form.audience === "CUSTOM" && <div className="col-span-2"><OptionPicker resource="message-recipients" label="接收老板 / 陪玩业务账号" multiple value={form.recipientAccountIds} onChange={value => change("recipientAccountIds", value as number[])} /><p className="mt-2 text-xs text-muted-foreground">具体账号选择器仅返回老板和陪玩；员工请使用“全体员工”范围。</p></div>}
            <Field field={{ key: "manual-push-title-field", label: "标题（最多 100 字）", required: true }} value={form.title} onChange={value => change("title", String(value))} />
            <Field field={{ key: "manual-push-preview", label: "摘要（最多 255 字）" }} value={form.preview} onChange={value => change("preview", String(value))} />
            <Field field={{ key: "manual-push-detail", label: "详情正文", type: "textarea", full: true }} value={form.detail} onChange={value => change("detail", String(value))} />
          </div>
        </FormSection>
        <aside className="rounded-xl border bg-muted/20 p-4 text-sm">
          <p className="mb-4 text-xs text-muted-foreground">通知预览</p>
          <p className="font-semibold break-words">{form.title || "尚未填写标题"}</p>
          <p className="mt-2 whitespace-pre-wrap break-words">{form.preview}</p>
          <p className="mt-3 text-xs text-muted-foreground">接收范围：{form.audience === "CUSTOM" ? `${form.recipientAccountIds.length} 个所选账号` : audienceLabels[form.audience]}</p>
          {form.audience === "ALL" && <p className="mt-3 text-xs text-amber-700 dark:text-amber-400">全员广播包含符合后端条件的老板、陪玩和员工业务账号。</p>}
        </aside>
      </div>
      <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1 size-4 accent-primary" checked={reviewed} onChange={event => setReviewed(event.target.checked)} />已核对消息内容与接收范围，确认创建批次</label>
    </MutationDialog>
  )
}

function ManualPushCampaignDetailView({ campaignId, canWrite, onClose, onChanged, onPushDenied, onReadDenied }: {
  campaignId: number
  canWrite: boolean
  onClose: () => void
  onChanged: () => Promise<void>
  onPushDenied: () => void
  onReadDenied: () => void
}) {
  const [status, setStatus] = useState<"" | ManualPushRecipientFilter>("")
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [selected, setSelected] = useState<number[]>([])
  const [retryBusy, setRetryBusy] = useState(false)
  const [retryError, setRetryError] = useState("")
  const [retryUnknown, setRetryUnknown] = useState(false)
  const result = useLoad(
    () => fetchCampaignStatus({ campaignId, page, pageSize, status }).catch(cause => {
      if ((cause as { status?: number })?.status === 403) onReadDenied()
      throw cause
    }),
    [campaignId, page, pageSize, status],
  )
  useEffect(() => {
    setPage(1)
    setSelected([])
    setRetryUnknown(false)
  }, [campaignId, status])
  useEffect(() => setPage(1), [pageSize])
  useEffect(() => {
    if (!result.data || result.error || result.loading) return
    setRetryUnknown(false)
    const terminal = new Set(result.data.list.filter(row => !isFailed(row)).map(row => row.id))
    setSelected(old => old.filter(id => !terminal.has(id)))
  }, [result.data, result.error, result.loading])
  const mode = manualPushReadMode({ hasSnapshot: !!result.data, loading: result.loading, error: result.error, itemCount: result.data?.list.length })
  const campaign = result.data?.campaign
  const visibleFailed = result.data?.list.filter(isFailed).map(row => row.id) || []

  async function retry() {
    if (!campaign || !canWrite || !selected.length || selected.length > 200 || retryBusy || retryUnknown) return
    setRetryBusy(true)
    setRetryError("")
    try {
      await postManualPushRetry(campaign, selected)
      toast.success(`已在原批次 #${campaign.campaign_id} 处理 ${selected.length} 个接收记录；请按最新逐人状态核对`)
      setSelected([])
      await result.reload()
      await onChanged()
    } catch (cause) {
      const failure = manualPushWriteFailure(cause)
      if ((cause as { status?: number })?.status === 403) onPushDenied()
      if (failure.kind === "unknown") {
        setRetryUnknown(true)
        setRetryError(`${manualPushErrorMessage(cause)} 重试结果未知，请先刷新原批次；不会创建新批次，也不要盲目再次提交。`)
      } else setRetryError(manualPushErrorMessage(cause))
    } finally {
      setRetryBusy(false)
    }
  }

  return (
    <section className="space-y-4 rounded-xl border bg-background p-5" aria-labelledby="manual-push-detail-title" data-manual-push-detail={campaignId}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><h3 id="manual-push-detail-title" className="text-lg font-semibold">批次 #{campaignId} · 逐人处理状态</h3><p className="mt-1 text-xs text-muted-foreground">失败重试沿用原 campaign，不创建新批次；每次最多 200 个接收记录。</p></div>
        <div className="flex gap-2"><Button type="button" size="sm" variant="outline" onClick={() => void result.reload()} disabled={result.loading || retryBusy}><RefreshCw className={result.loading ? "animate-spin" : ""} />刷新状态</Button><Button type="button" size="sm" variant="ghost" onClick={onClose} disabled={retryBusy}>关闭详情</Button></div>
      </div>
      <ReadErrorNotice error={result.error} retry={() => void result.reload()} hasSnapshot={!!result.data} />
      {mode === "stale" && <p role="status" className="rounded-lg border border-amber-500/40 bg-amber-500/5 px-4 py-3 text-sm">逐人状态为旧快照，统计、筛选与重试资格可能已经过期。</p>}
      {mode === "loading" ? <p role="status" className="rounded-lg bg-muted/20 p-6 text-sm text-muted-foreground">正在读取原批次和逐人状态…</p>
        : mode === "error" ? <p className="rounded-lg bg-muted/20 p-6 text-sm text-muted-foreground">批次详情尚未读取，不会以空列表或零统计代替。</p>
        : result.data && campaign ? (
          <>
            <CampaignSummary campaign={campaign} />
            <div className="flex flex-wrap items-end justify-between gap-3 border-t pt-4">
              <Field field={{ key: "manual-recipient-status", label: "逐人状态筛选", type: "select", options: [["CREATED", "站内已创建"], ["PENDING", "全部待处理"], ["FAILED", "仅失败（待处理子集）"], ["SKIPPED", "已跳过"]] }} value={status} onChange={value => setStatus(value as "" | ManualPushRecipientFilter)} />
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs text-muted-foreground">已选 {selected.length} / 200 个失败记录</span>
                <Button type="button" size="sm" variant="outline" disabled={!visibleFailed.length || retryBusy} onClick={() => {
                  try { setSelected(updateManualRetrySelection(selected, visibleFailed, true)); setRetryError("") }
                  catch (cause) { setRetryError(manualPushErrorMessage(cause)) }
                }}>选择本页失败记录</Button>
                <Button type="button" size="sm" variant="ghost" disabled={!selected.length || retryBusy} onClick={() => setSelected([])}>清空选择</Button>
                <Button type="button" size="sm" onClick={() => void retry()} disabled={!canWrite || !selected.length || retryBusy || retryUnknown || !!result.error}>{retryUnknown ? "先刷新原批次核对" : retryBusy ? "正在处理…" : "重试所选失败记录"}</Button>
              </div>
            </div>
            {!canWrite && <p className="text-xs text-muted-foreground">当前为只读状态，失败重试需要有效的 message:push 权限。</p>}
            <Feedback message={retryError} />
            {mode === "empty" ? <p role="status" className="rounded-lg bg-muted/20 p-6 text-center text-sm text-muted-foreground">{status ? "当前状态筛选没有接收记录。" : "该批次没有接收记录；这是成功的空批次，不代表统计缺失。"}</p> : (
              <div className="overflow-x-auto"><table className="w-full min-w-[1000px] text-sm"><caption className="sr-only">批次 {campaignId} 逐人接收记录</caption><thead><tr className="border-b text-left text-xs text-muted-foreground"><th className="py-3 pr-4 font-medium">选择</th><th className="py-3 pr-4 font-medium">接收记录</th><th className="py-3 pr-4 font-medium">业务账号</th><th className="py-3 pr-4 font-medium">状态</th><th className="py-3 pr-4 font-medium">尝试次数</th><th className="py-3 pr-4 font-medium">最近错误</th><th className="py-3 pr-4 font-medium">站内消息 ID</th><th className="py-3 font-medium">下次处理时间</th></tr></thead><tbody>{result.data.list.map(row => <RecipientRow key={row.id} row={row} selected={selected.includes(row.id)} disabled={!canWrite || retryBusy || retryUnknown} onChange={checked => {
                try { setSelected(updateManualRetrySelection(selected, [row.id], checked)); setRetryError("") }
                catch (cause) { setRetryError(manualPushErrorMessage(cause)) }
              }} />)}</tbody></table></div>
            )}
            <Pagination snapshotAvailable page={result.data.page} total={result.data.total} pageSize={result.data.page_size} loading={result.loading} onChange={setPage} onPageSizeChange={setPageSize} />
          </>
        ) : null}
    </section>
  )
}

function CampaignSummary({ campaign }: { campaign: ManualPushCampaignDetail }) {
  const stats: Array<[string, number, string]> = [
    ["接收人总数", campaign.total, "total"],
    ["站内已创建", campaign.count, "count"],
    ["待处理", campaign.pending_count, "pending_count"],
    ["其中失败", campaign.failed_count, "failed_count（pending 子集）"],
    ["已跳过", campaign.skipped_count, "skipped_count"],
  ]
  return <div className="space-y-4"><div><h4 className="font-semibold break-words">{campaign.title}</h4><p className="mt-1 text-xs text-muted-foreground">{typeLabels[campaign.message_type]} · {selectorLabel(campaign)} · 操作人 #{campaign.creator_id}</p><p className="mt-1 break-all font-mono text-xs text-muted-foreground">{campaign.client_request_id}</p></div><dl className="grid grid-cols-2 gap-3 md:grid-cols-5">{stats.map(([label, value, note]) => <div key={note} className="rounded-lg bg-muted/20 p-3"><dt className="text-xs text-muted-foreground">{label}</dt><dd className="mt-1 text-xl font-semibold tabular-nums">{value}</dd><dd className="mt-1 break-all text-[10px] text-muted-foreground">{note}</dd></div>)}</dl><div className="rounded-lg border p-4 text-sm"><p className="whitespace-pre-wrap break-words text-muted-foreground">{campaign.preview || "无摘要"}</p>{campaign.detail && <p className="mt-3 whitespace-pre-wrap break-words leading-7">{campaign.detail}</p>}</div><p className="text-xs text-muted-foreground">count / CREATED 只证明站内消息记录已经创建，不证明 WebSocket 或外部渠道送达；failed_count 已包含在 pending_count 中。</p></div>
}
function isFailed(row: ManualPushRecipient) {
  return row.status === "PENDING" && row.last_error !== ""
}
function RecipientRow({ row, selected, disabled, onChange }: { row: ManualPushRecipient; selected: boolean; disabled: boolean; onChange: (checked: boolean) => void }) {
  const failed = isFailed(row)
  return <tr className="border-b last:border-0"><td className="py-3 pr-4">{failed ? <input type="checkbox" aria-label={`选择失败接收记录 ${row.id}`} className="size-4 accent-primary" checked={selected} disabled={disabled} onChange={event => onChange(event.target.checked)} /> : <span className="text-muted-foreground">—</span>}</td><td className="py-3 pr-4 font-mono">#{row.id}</td><td className="py-3 pr-4"><span className="font-mono">{row.account_no || "账号编号为空"}</span><p className="text-xs text-muted-foreground">account_id: {row.account_id}</p></td><td className="py-3 pr-4"><Badge variant={row.status === "CREATED" ? "secondary" : failed ? "destructive" : "outline"}>{row.status === "CREATED" ? "站内已创建" : row.status === "SKIPPED" ? "已跳过" : failed ? "失败待处理" : "待处理"}</Badge></td><td className="py-3 pr-4 tabular-nums">{row.attempts}</td><td className="max-w-52 whitespace-normal py-3 pr-4 text-xs">{row.last_error || "—"}</td><td className="py-3 pr-4 font-mono">{row.message_id ?? "—"}</td><td className="py-3">{row.next_attempt_at ? formatDateTime(row.next_attempt_at) : "—"}</td></tr>
}
