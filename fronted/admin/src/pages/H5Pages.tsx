import { useMemo, useRef, useState } from "react"
import { AlertTriangle, Copy, ExternalLink, Eye, History, RefreshCw, Send, ShieldCheck } from "lucide-react"
import { toast } from "sonner"
import { getSession, useCurrentStore, useResource, type StoreInfo } from "@xa/shared"
import { request, requestId } from "@/api/request"
import { useAdminAuth } from "@/lib/auth"
import { formatDateTime } from "@/lib/format"
import {
  H5_PAGE_KINDS,
  H5_PAGE_LABELS,
  createH5WriteIntent,
  h5ErrorMessage,
  h5PublicUrl,
  h5WriteMessage,
  parseH5MutationResult,
  parseH5PageDirectory,
  parseH5Preview,
  parseH5ReceiptList,
  parseH5VersionDetail,
  parseH5VersionList,
  parseH5WriteIntent,
  submitH5Write,
  type H5ComplianceReceipt,
  type H5PageKind,
  type H5Snapshot,
  type H5StoreContext,
  type H5VersionSummary,
  type H5WithdrawResult,
  type H5WriteIntent,
} from "@/lib/h5-pages"
import { Feedback, LoadingPanel, PageHeader, ReadOnlyNotice } from "@/components/business"
import { ReadErrorNotice } from "@/components/page-data-state"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

async function fetchH5PageDirectory() {
  return parseH5PageDirectory((await request<unknown>({ url: "/h5-pages/" })).data)
}
async function fetchH5Preview(kind: H5PageKind, storeName: string) {
  return parseH5Preview((await request<unknown>({ url: `/h5-pages/${kind}/preview/` })).data, { kind, storeName })
}
async function fetchH5Versions(kind: H5PageKind, latestRevision: number) {
  return parseH5VersionList((await request<unknown>({ url: `/h5-pages/${kind}/versions/` })).data, { latestRevision })
}
async function fetchH5VersionDetail(kind: H5PageKind, version: number, storeName: string) {
  return parseH5VersionDetail((await request<unknown>({ url: `/h5-pages/${kind}/versions/${version}/` })).data, { kind, storeName, version })
}
async function fetchH5Receipts(kind: H5PageKind, version: number, receiptIds: number[]) {
  return parseH5ReceiptList((await request<unknown>({ url: `/h5-pages/${kind}/versions/${version}/receipts/` })).data, { receiptIds })
}
async function sendH5Write(intent: H5WriteIntent) {
  const path = `/h5-pages/${intent.kind}/${intent.operation}/`
  const result = await request<unknown>({ method: "POST", url: path, data: intent.payload, reviewPath: "/h5-pages" })
  return parseH5MutationResult(result.data, intent)
}

const complianceLabels: Record<string, string> = {
  STORE_NAME: "门店名称",
  STORE_INTRODUCTION: "门店介绍",
  SERVICE_RULES: "服务规则",
  SERVICE_DETAILS: "服务详情",
}

export default function H5PagesPage() {
  const store = useCurrentStore()
  const auth = useAdminAuth()
  const session = getSession("admin")
  const scope = `${store.slug}:${session.sessionId}:${auth.revision}`
  return (
    <H5PagesContent
      key={scope}
      store={store}
      sessionId={session.sessionId}
      canEdit={auth.hasPerm("engagement:edit")}
      canViewReceipts={auth.hasPerm("content:view")}
    />
  )
}

function H5PagesContent({ store, sessionId, canEdit, canViewReceipts }: {
  store: StoreInfo
  sessionId: string
  canEdit: boolean
  canViewReceipts: boolean
}) {
  const storeContext: H5StoreContext = { slug: store.slug, name: store.name, bossUrl: store.boss_url }
  const storageKey = `xa:fronted:admin:h5-write:${store.slug}:${sessionId}`
  const identity = useMemo(() => ({ storeSlug: store.slug, sessionId }), [store.slug, sessionId])
  const restored = useMemo(() => {
    try {
      if (typeof sessionStorage === "undefined") return { intent: null, error: "" }
      return { intent: parseH5WriteIntent(sessionStorage.getItem(storageKey), identity), error: "" }
    } catch (cause) {
      return { intent: null, error: h5ErrorMessage(cause) }
    }
  }, [storageKey, identity])
  const [pending, setPending] = useState<H5WriteIntent | null>(restored.intent)
  const [recoveryError, setRecoveryError] = useState(restored.error)
  const [selectedKind, setSelectedKind] = useState<H5PageKind>("PRICE_LIST")
  const [dialog, setDialog] = useState<"publish" | "withdraw" | null>(null)
  const [copyrightConfirmed, setCopyrightConfirmed] = useState(false)
  const [reason, setReason] = useState("")
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState("")
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null)
  const [confirmedWithdrawal, setConfirmedWithdrawal] = useState<H5WithdrawResult | null>(null)
  const writeLock = useRef(false)

  const directory = useResource(() => fetchH5PageDirectory(), [])
  const preview = useResource(() => fetchH5Preview(selectedKind, store.name), [selectedKind])
  const directoryRows = directory.data?.list.map(item =>
    confirmedWithdrawal && item.kind === confirmedWithdrawal.kind &&
    item.revision === confirmedWithdrawal.revision && item.currentVersion === confirmedWithdrawal.withdrawnVersion
      ? { ...item, published: false, currentVersion: null }
      : item,
  )
  const page = directoryRows?.find(item => item.kind === selectedKind)
  const currentPreview = preview.data?.kind === selectedKind ? preview.data : undefined
  const hasDirectory = !!directory.data
  const fresh = !directory.error && !preview.error
  const blocked = !!pending || !!recoveryError

  function saveIntent(intent: H5WriteIntent) {
    sessionStorage.setItem(storageKey, JSON.stringify(intent))
    setPending(intent)
    setRecoveryError("")
  }
  function clearIntent() {
    sessionStorage.removeItem(storageKey)
    setPending(null)
  }
  async function refresh() {
    setActionError("")
    const [rows] = await Promise.all([directory.reload(), preview.reload()])
    if (rows) setConfirmedWithdrawal(null)
  }
  async function runWrite(intent: H5WriteIntent, recovering = false) {
    if (writeLock.current) return
    writeLock.current = true
    setBusy(true)
    setActionError("")
    try {
      const result = await submitH5Write(intent, {
        save: saveIntent,
        send: sendH5Write,
        clear: clearIntent,
      }, { recovering })
      setDialog(null)
      setSelectedKind(intent.kind)
      if ("withdrawnVersion" in result) setConfirmedWithdrawal(result)
      toast.success(
        intent.operation === "publish"
          ? `${H5_PAGE_LABELS[intent.kind]} v${result.version} 已发布${result.replayed ? "（恢复原回执）" : ""}`
          : `${H5_PAGE_LABELS[intent.kind]} v${"withdrawnVersion" in result ? result.withdrawnVersion : "待核对"} 已撤回${result.replayed ? "（恢复原回执）" : ""}`,
      )
      const refreshed = await directory.reload()
      if (refreshed) setConfirmedWithdrawal(null)
      if (selectedKind === intent.kind) await preview.reload()
    } catch (cause) {
      setDialog(null)
      setActionError(h5WriteMessage(cause, recovering))
      const code = (cause as { errorCode?: string; body?: { errorCode?: string } })?.errorCode ||
        (cause as { body?: { errorCode?: string } })?.body?.errorCode
      if (code === "H5_REVISION_CONFLICT" || code === "H5_PAGE_NOT_PUBLISHED") {
        await directory.reload()
        if (selectedKind === intent.kind) await preview.reload()
      }
    } finally {
      writeLock.current = false
      setBusy(false)
    }
  }
  function openPublish() {
    if (!page || !currentPreview || blocked || busy || !fresh) return
    setCopyrightConfirmed(false)
    setActionError("")
    setDialog("publish")
  }
  function openWithdraw() {
    if (!page || blocked || busy || directory.error) return
    setReason("")
    setActionError("")
    setDialog("withdraw")
  }
  async function confirmPublish() {
    if (!page || !currentPreview?.publishable || !copyrightConfirmed || blocked || busy || !fresh) return
    const intent = createH5WriteIntent(identity, selectedKind, "publish", page.revision, requestId("h5-publish"))
    await runWrite(intent)
  }
  async function confirmWithdraw() {
    if (!page || !reason.trim() || blocked || busy || directory.error) return
    try {
      const intent = createH5WriteIntent(identity, selectedKind, "withdraw", page.revision, requestId("h5-withdraw"), reason)
      await runWrite(intent)
    } catch (cause) {
      setActionError(h5ErrorMessage(cause))
    }
  }
  function changeKind(value: string) {
    if (busy || !H5_PAGE_KINDS.includes(value as H5PageKind)) return
    setSelectedKind(value as H5PageKind)
    setSelectedVersion(null)
    setActionError("")
  }

  let publicUrl = ""
  let publicUrlError = ""
  if (page?.published && page.publicPath) {
    try { publicUrl = h5PublicUrl(page.publicPath, storeContext) }
    catch (cause) { publicUrlError = h5ErrorMessage(cause) }
  }
  const publicActionsEnabled = !!publicUrl && !directory.error && !busy

  return (
    <div className="space-y-6">
      <PageHeader title="H5 内容发布" tag={store.name}>
        <Button type="button" variant="outline" onClick={() => void refresh()} disabled={busy || directory.loading || preview.loading}>
          <RefreshCw className={directory.loading || preview.loading ? "animate-spin" : ""} />
          刷新内容
        </Button>
      </PageHeader>
      <p className="text-sm text-muted-foreground">
        当前门店：{store.name}（{store.slug}）。发布内容来自本店服务、下单须知和门店资料；历史版本不可修改。
      </p>
      <ReadOnlyNotice canEdit={canEdit} subject="H5 页面预览、公开状态与历史版本" />

      {recoveryError && (
        <div role="alert" className="flex items-start gap-3 rounded-xl border border-destructive/40 bg-destructive/5 p-4 text-sm">
          <AlertTriangle className="mt-0.5 size-5 shrink-0 text-destructive" />
          <div><strong className="block">待核对记录不可读取，已暂停所有写入</strong><p className="mt-1 text-muted-foreground">{recoveryError}</p></div>
        </div>
      )}
      {pending && (
        <section role="alert" className="rounded-xl border border-amber-500/50 bg-amber-500/5 p-4" data-h5-pending={pending.operation}>
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0">
              <h2 className="font-semibold">{H5_PAGE_LABELS[pending.kind]} {pending.operation === "publish" ? "发布" : "撤回"}结果未知</h2>
              <p className="mt-1 text-sm text-muted-foreground">只能使用原请求编号和原内容恢复，不能重新生成一次发布来覆盖未知结果。</p>
              <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                <div><dt className="text-muted-foreground">门店</dt><dd>{pending.storeSlug}</dd></div>
                <div><dt className="text-muted-foreground">写入前版本</dt><dd>revision {pending.payload.expected_version}</dd></div>
                <div className="sm:col-span-2"><dt className="text-muted-foreground">请求编号</dt><dd className="break-all font-mono">{pending.payload.client_request_id}</dd></div>
              </dl>
            </div>
            <Button type="button" onClick={() => void runWrite(pending, true)} disabled={busy || !canEdit}>
              <RefreshCw className={busy ? "animate-spin" : ""} />使用原请求核对结果
            </Button>
          </div>
          {!canEdit && <p className="mt-3 text-xs text-muted-foreground">当前账号缺少 engagement:edit，恢复权限后再核对原请求。</p>}
        </section>
      )}
      <Feedback message={actionError} />

      <ReadErrorNotice error={directory.error?.message} retry={() => void directory.reload()} hasSnapshot={hasDirectory} />
      {directory.error && directory.data && (
        <p role="status" className="rounded-lg border border-amber-500/40 bg-amber-500/5 px-4 py-3 text-sm">
          当前展示的是旧目录快照，公开状态可能已经过期；写入、打开和复制公开链接均已暂停。
        </p>
      )}
      {!directory.data && directory.loading ? <LoadingPanel /> : !directory.data && directory.error ? (
        <section className="rounded-xl border p-8 text-center">
          <h2 className="text-lg font-semibold">{directory.error instanceof Error && "status" in directory.error && directory.error.status === 403 ? "没有查看 H5 内容发布的权限" : "H5 内容目录尚未读取"}</h2>
          <p className="mt-2 text-sm text-muted-foreground">请核对 engagement:view 权限、门店入口和网络后重试。</p>
          <Button className="mt-4" variant="outline" onClick={() => void directory.reload()}>重新读取</Button>
        </section>
      ) : directory.data && page ? (
        <>
          <Tabs value={selectedKind} onValueChange={changeKind}>
            <TabsList className="grid h-auto w-full grid-cols-3 rounded-xl" aria-label="H5 页面类型">
              {directoryRows?.map(item => (
                <TabsTrigger key={item.kind} value={item.kind} disabled={busy} className="min-w-0 px-3 py-2">
                  <span className="truncate">{item.label}</span>
                  <Badge variant="outline" className="ml-1 text-[10px]">
                    {directory.error ? "待核对" : item.published ? `在线 v${item.currentVersion}` : item.revision ? "已撤回" : "未发布"}
                  </Badge>
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>

          <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
            <section className="min-w-0 rounded-xl border bg-card p-5" aria-labelledby="h5-preview-title">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h2 id="h5-preview-title" className="text-lg font-semibold">发布前预览 · {page.label}</h2>
                  <p className="mt-1 text-xs text-muted-foreground">预览是当前来源内容；发布后形成不可修改的文字快照。</p>
                </div>
                <Button type="button" size="sm" variant="outline" onClick={() => void preview.reload()} disabled={busy || preview.loading}>
                  <RefreshCw className={preview.loading ? "animate-spin" : ""} />刷新预览
                </Button>
              </div>
              <div className="mt-4"><ReadErrorNotice error={preview.error?.message} retry={() => void preview.reload()} hasSnapshot={!!currentPreview} /></div>
              {preview.error && currentPreview && <p role="status" className="mt-3 text-sm text-amber-700 dark:text-amber-400">以下为旧预览快照，可能已过期，发布已暂停。</p>}
              {!currentPreview && preview.loading ? <div className="mt-6"><LoadingPanel variant="detail" /></div> : !currentPreview && preview.error ? (
                <div className="mt-6 rounded-lg bg-muted/20 p-6 text-sm text-muted-foreground">预览尚未读取，请重试；不会根据目录猜测可发布内容。</div>
              ) : currentPreview ? (
                <>
                  <div className="mt-4 flex flex-wrap items-center gap-2">
                    <Badge variant={currentPreview.publishable ? "secondary" : "destructive"}>{currentPreview.publishable ? "来源可发布" : "来源需处理"}</Badge>
                    <span className="text-xs text-muted-foreground">草稿摘要 {currentPreview.draftDigest.slice(0, 12)}…</span>
                  </div>
                  {currentPreview.issues.length > 0 && (
                    <ul className="mt-4 list-disc space-y-1 rounded-lg border border-destructive/30 bg-destructive/5 p-4 pl-8 text-sm">
                      {currentPreview.issues.map(issue => <li key={issue}>{issue}</li>)}
                    </ul>
                  )}
                  <div className="mt-5"><SnapshotView snapshot={currentPreview.snapshot} /></div>
                </>
              ) : null}
              <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t pt-4">
                <p className="text-xs text-muted-foreground">写入基于 revision {page.revision}；版本冲突不会被自动重试。</p>
                <div className="flex flex-wrap gap-2">
                  <Button type="button" variant="destructive" onClick={openWithdraw} disabled={!canEdit || !page.published || blocked || busy || !!directory.error} title={!page.published ? "当前没有可撤回的在线版本" : blocked ? "请先核对原请求" : undefined}>
                    撤回当前版本
                  </Button>
                  <Button type="button" onClick={openPublish} disabled={!canEdit || !currentPreview?.publishable || page.sourceStatus !== "READY" || blocked || busy || !fresh} title={!currentPreview?.publishable ? "请先处理来源内容问题" : blocked ? "请先核对原请求" : !fresh ? "旧快照不可用于发布" : undefined}>
                    <Send />发布新版本
                  </Button>
                </div>
              </div>
            </section>

            <aside className="min-w-0 space-y-4 rounded-xl border bg-card p-5" aria-labelledby="h5-public-status">
              <div className="flex items-center justify-between gap-3">
                <h2 id="h5-public-status" className="font-semibold">公开状态</h2>
                <Badge variant={page.published && !directory.error ? "secondary" : "outline"}>
                  {directory.error ? "状态已过期" : page.published ? "在线" : page.revision ? "已撤回" : "未发布"}
                </Badge>
              </div>
              <dl className="space-y-3 text-sm">
                <div className="flex justify-between gap-3"><dt className="text-muted-foreground">当前版本</dt><dd>{page.published ? `v${page.currentVersion}` : "无在线版本"}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-muted-foreground">写入版本号</dt><dd>revision {page.revision}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-muted-foreground">来源状态</dt><dd>{page.sourceStatus === "READY" ? "可发布" : "需处理"}</dd></div>
              </dl>
              {page.published ? (
                <div className="space-y-3 border-t pt-4">
                  <Label htmlFor="h5-public-url">公开页面地址</Label>
                  <Input id="h5-public-url" readOnly value={publicUrl || publicUrlError || "公开地址不可用"} onFocus={event => event.target.select()} />
                  {publicUrlError && <p role="alert" className="text-xs text-destructive">{publicUrlError}</p>}
                  {directory.error && <p className="text-xs text-amber-700 dark:text-amber-400">旧公开地址仅供辨认，状态核实前不可打开或复制。</p>}
                  <div className="flex gap-2">
                    <Button type="button" size="sm" variant="outline" disabled={!publicActionsEnabled} onClick={() => void copyPublicUrl(publicUrl)}>
                      <Copy />复制链接
                    </Button>
                    {publicActionsEnabled ? (
                      <Button size="sm" asChild><a href={publicUrl} target="_blank" rel="noopener noreferrer"><ExternalLink />打开页面</a></Button>
                    ) : <Button size="sm" disabled><ExternalLink />打开页面</Button>}
                  </div>
                  <p className="break-all text-xs text-muted-foreground">公开 JSON：{page.jsonPath}</p>
                </div>
              ) : (
                <div className="rounded-lg bg-muted/20 p-4 text-sm text-muted-foreground">
                  {page.revision ? "当前版本已撤回，旧公开地址已失效。历史快照只读保留，再次发布会形成新版本。" : "尚未发布，没有可公开访问的页面。"}
                </div>
              )}
            </aside>
          </div>

          <H5VersionHistory
            key={`${store.slug}:${selectedKind}`}
            kind={selectedKind}
            latestRevision={page.revision}
            onOpen={setSelectedVersion}
          />
        </>
      ) : null}

      <AlertDialog open={dialog === "publish"} onOpenChange={open => { if (!open && !busy) setDialog(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认发布{H5_PAGE_LABELS[selectedKind]}？</AlertDialogTitle>
            <AlertDialogDescription>将以当前门店 {store.name} 的 revision {page?.revision} 发布新版本。发布后内容不可修改，只能再次发布或撤回。</AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-4">
            <div className="rounded-lg bg-muted/20 p-3 text-xs text-muted-foreground">草稿摘要：{currentPreview?.draftDigest || "未读取"}</div>
            <label className="flex cursor-pointer items-start gap-3 text-sm leading-6">
              <Checkbox checked={copyrightConfirmed} onCheckedChange={value => setCopyrightConfirmed(value === true)} disabled={busy} aria-label="确认页面文字素材权利" />
              <span>我确认本页文字、价格与门店素材具有合法使用权，并已核对公开范围。</span>
            </label>
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>取消</AlertDialogCancel>
            <AlertDialogAction disabled={!copyrightConfirmed || busy || !currentPreview?.publishable} onClick={event => { event.preventDefault(); void confirmPublish() }}>
              {busy ? "正在发布…" : "确认发布"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={dialog === "withdraw"} onOpenChange={open => { if (!open && !busy) setDialog(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>明确确认撤回{H5_PAGE_LABELS[selectedKind]}？</AlertDialogTitle>
            <AlertDialogDescription>撤回后 v{page?.currentVersion} 的公开页面和 JSON 将不可访问；历史快照仍保留，不能继续把旧地址称为在线。</AlertDialogDescription>
          </AlertDialogHeader>
          <label className="space-y-2 text-sm">
            <span>撤回原因（必填，最多 255 字）</span>
            <Textarea value={reason} onChange={event => setReason(event.target.value)} maxLength={255} disabled={busy} placeholder="说明撤回原因，供审计核对" autoFocus />
          </label>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>保留在线</AlertDialogCancel>
            <AlertDialogAction variant="destructive" disabled={!reason.trim() || busy} onClick={event => { event.preventDefault(); void confirmWithdraw() }}>
              {busy ? "正在撤回…" : "确认撤回"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {selectedVersion !== null && (
        <H5VersionDetailDialog
          key={`${store.slug}:${selectedKind}:${selectedVersion}`}
          kind={selectedKind}
          version={selectedVersion}
          storeName={store.name}
          canViewReceipts={canViewReceipts}
          onClose={() => setSelectedVersion(null)}
        />
      )}
    </div>
  )
}

async function copyPublicUrl(value: string) {
  if (!value) return
  try {
    await navigator.clipboard.writeText(value)
    toast.success("公开链接已复制")
  } catch {
    toast.error("浏览器未允许复制，请在地址框中全选复制")
  }
}

function SnapshotView({ snapshot }: { snapshot: H5Snapshot }) {
  return (
    <div className="min-w-0 rounded-lg border bg-background p-5" data-h5-snapshot={snapshot.kind}>
      <div className="border-b pb-4">
        <h3 className="text-xl font-semibold">{snapshot.title}</h3>
        <p className="mt-1 text-sm text-muted-foreground">{snapshot.store.name} · 预览生成于 {formatDateTime(snapshot.generatedAt)}</p>
      </div>
      {snapshot.kind === "INTRODUCTION" ? (
        snapshot.body ? <p className="mt-5 whitespace-pre-wrap break-words text-sm leading-7">{snapshot.body}</p> : <EmptySnapshot text="门店介绍暂为空，不能发布。" />
      ) : snapshot.kind === "ORDER_NOTICE" ? (
        snapshot.sections.length ? <div className="divide-y">{snapshot.sections.map(section => (
          <article key={section.id} className="py-4"><h4 className="font-medium">{section.title || "未填写标题"}</h4><p className="mt-2 whitespace-pre-wrap break-words text-sm leading-7">{section.content || "未填写内容"}</p></article>
        ))}</div> : <EmptySnapshot text="暂无生效中的下单须知。" />
      ) : snapshot.items.length ? <div className="divide-y">{snapshot.items.map(item => (
        <article key={item.id} className="py-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div><h4 className="font-medium">{item.name || "未填写名称"}</h4><p className="mt-1 text-xs text-muted-foreground">{[item.gameName, item.categoryName].filter(Boolean).join(" · ") || "未分类"}</p></div>
            <p className="font-semibold text-primary">{item.priceText} / {item.priceUnit}</p>
          </div>
          <dl className="mt-3 space-y-3 text-sm leading-6">
            {([["简介", item.description], ["规则", item.rules], ["详情", item.details], ["下单说明", item.purchaseNote]] as const).filter(([, value]) => value).map(([label, value]) => (
              <div key={label}><dt className="font-medium">{label}</dt><dd className="mt-1 whitespace-pre-wrap break-words text-muted-foreground">{value}</dd></div>
            ))}
          </dl>
        </article>
      ))}</div> : <EmptySnapshot text="暂无公开且有标价的陪玩服务。" />}
    </div>
  )
}
function EmptySnapshot({ text }: { text: string }) {
  return <p role="status" className="mt-5 rounded-lg bg-muted/20 p-6 text-center text-sm text-muted-foreground">{text}</p>
}

function H5VersionHistory({ kind, latestRevision, onOpen }: { kind: H5PageKind; latestRevision: number; onOpen: (version: number) => void }) {
  const versions = useResource(() => latestRevision > 0
    ? fetchH5Versions(kind, latestRevision)
    : Promise.resolve({ list: [], total: 0 }), [kind, latestRevision])
  return (
    <section className="rounded-xl border bg-card p-5" aria-labelledby="h5-history-title">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><h2 id="h5-history-title" className="flex items-center gap-2 text-lg font-semibold"><History className="size-5" />历史版本</h2><p className="mt-1 text-xs text-muted-foreground">版本快照只读；撤回不会删除历史，也不能从历史页直接恢复上线。</p></div>
        {latestRevision > 0 && <Button type="button" size="sm" variant="outline" onClick={() => void versions.reload()} disabled={versions.loading}><RefreshCw className={versions.loading ? "animate-spin" : ""} />刷新历史</Button>}
      </div>
      <div className="mt-4"><ReadErrorNotice error={versions.error?.message} retry={() => void versions.reload()} hasSnapshot={!!versions.data} /></div>
      {versions.error && versions.data && <p role="status" className="mb-3 text-sm text-amber-700 dark:text-amber-400">以下历史目录是旧快照，可能已过期。</p>}
      {!versions.data && versions.loading ? <LoadingPanel /> : !versions.data && versions.error ? null : latestRevision === 0 || versions.data?.total === 0 ? (
        <p className="mt-4 rounded-lg bg-muted/20 p-6 text-center text-sm text-muted-foreground">暂无历史发布版本。</p>
      ) : versions.data ? (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[760px] text-sm">
            <caption className="sr-only">{H5_PAGE_LABELS[kind]}历史版本，只读</caption>
            <thead><tr className="border-b text-left text-xs text-muted-foreground"><th className="py-3 pr-4 font-medium">版本</th><th className="py-3 pr-4 font-medium">发布时间</th><th className="py-3 pr-4 font-medium">合规状态</th><th className="py-3 pr-4 font-medium">版权确认</th><th className="py-3 pr-4 font-medium">内容摘要</th><th className="py-3 text-right font-medium">操作</th></tr></thead>
            <tbody>{versions.data.list.map(item => (
              <tr key={item.version} className="border-b last:border-0"><td className="py-3 pr-4 font-medium">v{item.version}</td><td className="py-3 pr-4">{formatDateTime(item.publishedAt)}</td><td className="py-3 pr-4"><Badge variant="outline">{item.complianceStatus === "CHECKED" ? "已校验" : "门店已停用文本合规"}</Badge></td><td className="py-3 pr-4">已确认</td><td className="py-3 pr-4 font-mono text-xs">{item.contentSha256.slice(0, 12)}…</td><td className="py-3 text-right"><Button type="button" size="sm" variant="outline" onClick={() => onOpen(item.version)}><Eye />查看只读快照</Button></td></tr>
            ))}</tbody>
          </table>
        </div>
      ) : null}
    </section>
  )
}

function H5VersionDetailDialog({ kind, version, storeName, canViewReceipts, onClose }: {
  kind: H5PageKind
  version: number
  storeName: string
  canViewReceipts: boolean
  onClose: () => void
}) {
  const detail = useResource(() => fetchH5VersionDetail(kind, version, storeName), [kind, version, storeName])
  return (
    <Dialog open onOpenChange={open => { if (!open) onClose() }}>
      <DialogContent className="sm:max-w-4xl" closeDisabled={detail.loading && !detail.data}>
        <DialogHeader><DialogTitle>{H5_PAGE_LABELS[kind]} · v{version} 只读快照</DialogTitle><DialogDescription>历史版本不可编辑、删除或直接重新发布；公开状态以当前目录为准。</DialogDescription></DialogHeader>
        <div className="min-h-0 flex-1 overflow-y-auto p-6">
          <ReadErrorNotice error={detail.error?.message} retry={() => void detail.reload()} hasSnapshot={!!detail.data} />
          {!detail.data && detail.loading ? <LoadingPanel variant="detail" /> : detail.data ? (
            <div className="space-y-5">
              <VersionFacts item={detail.data} />
              <SnapshotView snapshot={detail.data.snapshot} />
              {detail.data.complianceReceiptIds.length === 0 ? <p className="rounded-lg bg-muted/20 p-4 text-sm text-muted-foreground">本版本没有内容合规回执。</p> : canViewReceipts ? (
                <H5ReceiptList kind={kind} version={version} receiptIds={detail.data.complianceReceiptIds} />
              ) : <p className="rounded-lg bg-muted/20 p-4 text-sm text-muted-foreground">当前账号没有 content:view 权限，因此未查询合规回执。</p>}
            </div>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  )
}
function VersionFacts({ item }: { item: H5VersionSummary }) {
  return <dl className="grid gap-3 rounded-lg bg-muted/20 p-4 text-xs sm:grid-cols-2"><div><dt className="text-muted-foreground">发布时间</dt><dd className="mt-1">{formatDateTime(item.publishedAt)}</dd></div><div><dt className="text-muted-foreground">合规状态</dt><dd className="mt-1">{item.complianceStatus === "CHECKED" ? "已校验" : "门店已停用文本合规"}</dd></div><div className="sm:col-span-2"><dt className="text-muted-foreground">内容 SHA-256</dt><dd className="mt-1 break-all font-mono">{item.contentSha256}</dd></div></dl>
}
function H5ReceiptList({ kind, version, receiptIds }: { kind: H5PageKind; version: number; receiptIds: number[] }) {
  const receipts = useResource(() => fetchH5Receipts(kind, version, receiptIds), [kind, version, JSON.stringify(receiptIds)])
  return (
    <section className="rounded-lg border p-4" aria-label={`v${version} 合规回执`}>
      <h3 className="flex items-center gap-2 text-sm font-semibold"><ShieldCheck className="size-4" />内容合规回执</h3>
      <div className="mt-3"><ReadErrorNotice error={receipts.error?.message} retry={() => void receipts.reload()} hasSnapshot={!!receipts.data} /></div>
      {!receipts.data && receipts.loading ? <p role="status" className="mt-4 text-sm text-muted-foreground">正在读取本版本回执…</p> : receipts.data?.list.length ? (
        <div className="mt-3 space-y-3">{receipts.data.list.map(item => <ReceiptCard key={item.id} item={item} />)}</div>
      ) : receipts.data ? <p className="mt-4 text-sm text-muted-foreground">本版本没有回执记录。</p> : null}
    </section>
  )
}
function ReceiptCard({ item }: { item: H5ComplianceReceipt }) {
  return <article className="rounded-md bg-muted/20 p-3 text-xs"><div className="flex flex-wrap justify-between gap-2"><strong>{complianceLabels[item.fieldName] || item.fieldName}</strong><span>回执 #{item.id} · 规则 v{item.ruleVersion}</span></div><dl className="mt-3 grid gap-2 sm:grid-cols-2"><div><dt className="text-muted-foreground">命中规则</dt><dd>{item.matchedRuleCodes.join("、") || "无"}</dd></div><div><dt className="text-muted-foreground">豁免规则</dt><dd>{item.exemptedRuleCodes.join("、") || "无"}</dd></div><div><dt className="text-muted-foreground">记录时间</dt><dd>{formatDateTime(item.createdAt)}</dd></div><div><dt className="text-muted-foreground">操作者标签</dt><dd>{item.actorLabel || "已脱敏"}</dd></div><div className="sm:col-span-2"><dt className="text-muted-foreground">内容摘要</dt><dd className="break-all font-mono">{item.contentSha256}</dd></div></dl></article>
}
