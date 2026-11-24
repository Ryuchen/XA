import { AdminPageSkeleton } from "@/components/AdminPageSkeleton"
import { DataRegion } from "@/components/page-data-state"
import { getStoreSlug } from "@xa/shared"
import { useEffect, useRef, useState } from "react"
import { Link, useSearchParams } from "react-router"
import {
  CalendarClock,
  ClipboardCheck,
  ExternalLink,
  FileText,
  Gamepad2,
  Headset,
  Link2,
  Search,
  UserRound,
  UsersRound,
  MessageCircleMore,
  RefreshCw,
  LoaderCircle,
  Send,
  ImagePlus,
  X,
} from "@/components/ui/icons"
import { toast } from "sonner"
import { getSession, useRealtime } from "@xa/shared"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { AdminFilters } from "@/components/AdminFilters"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Switch } from "@/components/ui/switch"
import { Avatar, AvatarImage, AvatarFallback } from "@/components/ui/avatar"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DataPageState,
  DataTable,
  Details,
  FormSection,
  ReadOnlyNotice,
  Feedback,
  Field,
  MutationDialog,
  OptionPicker,
  PageHeader,
  Pagination,
  RefreshButton,
  Status,
  useList,
  useLoad,
  useLiveReload,
  type Row,
  type FormField,
} from "@/components/business"
import PrivateImage from "@/components/PrivateImage"
import ConsultationAddonCase from "@/components/ConsultationAddonCase"
import { request, requestId } from "@/api/request"
import { useAdminAuth } from "@/lib/auth"
import { formatDateTime } from "@/lib/format"

function SupportHeader(props: Parameters<typeof PageHeader>[0]) {
  return <PageHeader {...props} tone="feature" />
}

export function EvaluationsPage() {
  const [params] = useSearchParams()
  const [draft, setDraft] = useState<Row>({
    keyword: "",
    only_unreplied: "",
    score: "",
    ...Object.fromEntries(params.entries()),
  })
  const [query, setQuery] = useState(draft)
  const list = useList("evaluations", query)
  const [operation, setOperation] = useState<{
    kind: "reply" | "delete"
    row: Row
  } | null>(null)
  const [content, setContent] = useState("")
  const [blocked, setBlocked] = useState(false)
  const auth = useAdminAuth()
  useEffect(() => {
    const next = {
      keyword: "",
      only_unreplied: "",
      score: "",
      ...Object.fromEntries(params.entries()),
    }
    setDraft(next)
    setQuery(next)
  }, [params])
  useLiveReload(list.reload)
  return (
    <DataPageState
      loading={list.loading}
      hasSnapshot={!!list.data}
      error={list.error}
      retry={list.reload}
      skeleton="evaluations"
    >
      {
        <div className="space-y-4">
          <SupportHeader
            title="订单评价"
            description="查看综合与技术、态度、沟通评分。回复和作废均核对当前版本。"
          >
            <RefreshButton loading={list.loading} reload={list.reload} />
          </SupportHeader>
          <Feedback message={list.error} retry={list.reload} />
          <DataTable snapshotAvailable={!!list.data}
            toolbar={
              <form
                onSubmit={(event) => {
                  event.preventDefault()
                  setQuery({ ...draft })
                }}
              >
                <AdminFilters activeCount={["order_no", "customer_username", "customer_nickname", "provider_username", "provider_nickname", "exclude_direct_test"].filter((key) => !!draft[key]).length} primary={<>
                <div className="w-80 space-y-2">
                  <Label htmlFor="evaluation-keyword">订单 / 老板 / 陪玩</Label>
                  <Input
                    id="evaluation-keyword"
                    value={draft.keyword ?? ""}
                    onChange={(event) =>
                      setDraft({ ...draft, keyword: event.target.value })
                    }
                  />
                </div>
                <Field
                  field={{
                    key: "evaluation-score",
                    label: "综合评分",
                    type: "select",
                    options: [1, 2, 3, 4, 5].map((score) => [score, `${score} 星`]),
                  }}
                  value={draft.score}
                  onChange={(value) => setDraft({ ...draft, score: value })}
                />
                <Field
                  field={{
                    key: "evaluation-reply",
                    label: "回复状态",
                    type: "select",
                    options: [["1", "仅未回复"]],
                  }}
                  value={draft.only_unreplied}
                  onChange={(value) =>
                    setDraft({ ...draft, only_unreplied: value })
                  }
                />
                </>} actions={<Button size="sm">查询</Button>}>
                  {[
                    { key: "order_no", label: "订单号" },
                    { key: "customer_username", label: "老板账号" },
                    { key: "customer_nickname", label: "老板昵称" },
                    { key: "provider_username", label: "陪玩账号" },
                    { key: "provider_nickname", label: "陪玩昵称" },
                    {
                      key: "exclude_direct_test",
                      label: "测试单范围",
                      type: "select",
                      options: [["1", "排除直接支付测试单"]],
                    },
                  ].map((field) => <Field key={field.key} field={field as FormField} value={draft[field.key]} onChange={(value) => setDraft((old) => ({ ...old, [field.key]: value }))} />)}
                </AdminFilters>
              </form>
            }
            rows={list.rows}
            error={list.error}
            retry={list.reload}
            loading={list.loading}
            columns={[
              {
                key: "order_no",
                label: "订单 / 服务",
                render: (row) => (
                  <>
                    <p>{row.order_no}</p>
                    <p className="text-xs text-muted-foreground">
                      {row.service_name}
                    </p>
                  </>
                ),
              },
              { key: "customer_name", label: "老板" },
              { key: "provider_name", label: "陪玩" },
              {
                key: "score",
                label: "评分",
                render: (row) => (
                  <>
                    <p>综合 {row.score} / 5</p>
                    <p className="text-xs text-muted-foreground">
                      技术 {row.skill_score} · 态度 {row.attitude_score} · 沟通{" "}
                      {row.communication_score}
                    </p>
                    {row.low_rating_aftersale_state && <p className="text-xs text-muted-foreground">
                      <Status value={row.low_rating_aftersale_state} label={({ OPENED: "低星：已创建售后", ATTACHED: "低星：已关联售后", CLOSED: "低星：售后已结案",
                          EXPIRED: "低星：售后期已结束", INELIGIBLE: "低星：订单不可发起售后" } as Record<string, string>)[row.low_rating_aftersale_state] || "低星待核对"} tone={row.low_rating_aftersale_state === "CLOSED" ? "muted" : undefined} />
                    </p>}
                    {row.voided_at && <p className="text-xs text-muted-foreground"><Status value="VOIDED" label="已作废" tone="muted" /> {row.void_reason}</p>}
                  </>
                ),
              },
              {
                key: "content",
                label: "评价 / 回复",
                render: (row) => (
                  <div className="max-w-lg space-y-2">
                    <p className="whitespace-pre-wrap">
                      {row.content || "未填写文字"}
                    </p>
                    {row.reply_content && (
                      <div className="rounded-md bg-muted p-2 text-xs leading-5">
                        <p>{row.reply_content}</p>
                        <p className="mt-1 text-muted-foreground">
                          {row.reply_author} · {formatDateTime(row.replied_at)}
                        </p>
                      </div>
                    )}
                  </div>
                ),
              },
              {
                key: "created_at",
                label: "评价时间",
                render: (row) => formatDateTime(row.created_at),
              },
              {
                key: "actions",
                label: "操作",
                render: (row) => (
                  <div className="flex gap-1">
                    {auth.hasPerm("evaluation:reply") && !row.voided_at && (
                      <Button
                        actionTone="edit"
                        variant="outline"
                        size="sm"
                        onClick={() => {
                          setOperation({ kind: "reply", row })
                          setContent(row.reply_content || "")
                          setBlocked(false)
                        }}
                      >
                        回复
                      </Button>
                    )}
                    {auth.hasPerm("evaluation:delete") && row.void_supported === true && !row.voided_at && (
                      <Button
                        actionTone="reject"
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          setOperation({ kind: "delete", row })
                          setContent("")
                          setBlocked(false)
                        }}
                      >
                        作废
                      </Button>
                    )}
                  </div>
                ),
              },
            ]}
          />
          <Pagination snapshotAvailable={!!list.data}
            page={list.page}
            total={list.total}
            loading={list.loading}
            onChange={list.setPage}
            pageSize={list.pageSize}
            onPageSizeChange={list.setPageSize}
          />
          {operation && (
            <MutationDialog
              title={operation.kind === "reply" ? "回复评价" : "作废评价"}
              description={`${operation.row.order_no} · ${operation.row.customer_name} → ${operation.row.provider_name} · 综合 ${operation.row.score} 星`}
              onClose={() => setOperation(null)}
              disabled={
                blocked || (operation.kind === "reply" ? !content.trim() : content.trim().length < 2)
              }
              onSubmit={async () => {
                try {
                  await request({
                    url: `/evaluations/${operation.row.id}/${operation.kind === "reply" ? "reply/" : ""}`,
                    method: operation.kind === "reply" ? "POST" : "DELETE",
                    data: {
                      reply_version: operation.row.reply_version,
                      ...(operation.kind === "reply"
                        ? { reply_content: content.trim() }
                        : { void_reason: content.trim() }),
                    },
                  })
                  toast.success(
                    operation.kind === "reply" ? "回复已保存" : "评价已作废"
                  )
                  void list.reload()
                } catch (cause: any) {
                  if (
                    cause.code === 409 ||
                    !cause.status ||
                    cause.status >= 500
                  )
                    setBlocked(true)
                  throw cause
                }
              }}
            >
              <p className="rounded-lg border bg-muted/40 p-4 text-sm leading-6">
                {operation.row.content || "未填写评价文字"}
              </p>
              {operation.kind === "reply" && (
                <Field
                  field={{
                    key: "evaluation-content",
                    label: "回复内容",
                    type: "textarea",
                    required: true,
                  }}
                  value={content}
                  onChange={setContent}
                />
              )}
              {operation.kind === "delete" && (
                <Field field={{ key: "evaluation-void-reason", label: "作废原因", type: "textarea", required: true }}
                  value={content} onChange={setContent} />
              )}
              {blocked && (
                <Feedback message="评价版本或处理结果已变化。请返回列表刷新，核对最新评价与回复后继续。" />
              )}
            </MutationDialog>
          )}
        </div>
      }
    </DataPageState>
  )
}

export function MessagesPage() {
  const auth = useAdminAuth()
  const [query, setQuery] = useState<Row>({ type: "" })
  const list = useList("messages", query)
  const [detail, setDetail] = useState<Row | null>(null)
  const [creating, setCreating] = useState(false)
  useLiveReload(list.reload)
  return (
    <DataPageState
      loading={list.loading}
      hasSnapshot={!!list.data}
      error={list.error}
      retry={list.reload}
      skeleton="messages"
    >
      {
        <div className="space-y-4">
          <SupportHeader
            title="站内消息"
            description="核对通知对象与内容，发送前再次确认接收范围。"
          >
            <RefreshButton loading={list.loading} reload={list.reload} />
            {auth.hasPerm("message:push") && (
              <Button size="sm" onClick={() => setCreating(true)}>
                发送站内消息
              </Button>
            )}
          </SupportHeader>
          <Feedback message={list.error} retry={list.reload} />
          <DataTable snapshotAvailable={!!list.data}
            toolbar={
              <AdminFilters primary={
                <Field
                  field={{
                    key: "message-type",
                    label: "消息类型",
                    type: "select",
                    options: [
                      ["SYSTEM", "系统通知"],
                      ["ORDER", "订单消息"],
                      ["SUPPORT", "客服消息"],
                      ["PROMOTION", "活动通知"],
                    ],
                  }}
                  value={query.type}
                  onChange={(value) => setQuery((old) => ({ ...old, type: value }))}
                />
              } />
            }
            rows={list.rows}
            error={list.error}
            retry={list.reload}
            loading={list.loading}
            columns={[
              {
                key: "title",
                label: "标题",
                render: (row) => (
                  <button
                    className="text-primary hover:underline"
                    onClick={() => setDetail(row)}
                  >
                    {row.title}
                  </button>
                ),
              },
              {
                key: "recipient_name",
                label: "接收人",
                render: (row) => (
                  <>
                    <p>{row.recipient_name}</p>
                    <p className="text-xs text-muted-foreground">
                      {row.recipient_no} · {row.recipient_username}
                    </p>
                  </>
                ),
              },
              { key: "type_display", label: "类型" },
              { key: "preview", label: "摘要" },
              {
                key: "is_read",
                label: "阅读状态",
                render: (row) => <Status value={row.is_read == null ? undefined : row.is_read ? "READ" : "UNREAD_MESSAGE"} label={row.is_read == null ? undefined : row.is_read ? "已读" : "未读"} tone={row.is_read ? "muted" : "primary"} />,
              },
              {
                key: "created_at",
                label: "发送时间",
                render: (row) => formatDateTime(row.created_at),
              },
            ]}
          />
          <Pagination snapshotAvailable={!!list.data}
            page={list.page}
            total={list.total}
            loading={list.loading}
            onChange={list.setPage}
            pageSize={list.pageSize}
            onPageSizeChange={list.setPageSize}
          />
          <Dialog
            open={!!detail}
            onOpenChange={(value) => {
              if (!value) setDetail(null)
            }}
          >
            <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
              <DialogHeader>
                <DialogTitle>{detail?.title}</DialogTitle>
                <DialogDescription>
                  {detail?.recipient_name} ·{" "}
                  {formatDateTime(detail?.created_at)}
                </DialogDescription>
              </DialogHeader>
              <p className="text-sm leading-6">{detail?.preview}</p>
              {detail?.detail && (
                <p className="text-sm leading-7 whitespace-pre-wrap">
                  {detail.detail}
                </p>
              )}
            </DialogContent>
          </Dialog>
          {creating && (
            <PushMessage
              onClose={() => setCreating(false)}
              onSaved={list.reload}
            />
          )}
        </div>
      }
    </DataPageState>
  )
}
function PushMessage({
  onClose,
  onSaved,
}: {
  onClose: () => void
  onSaved: () => void
}) {
  const auth = useAdminAuth()
  const marker = `xa:store:${getStoreSlug()}:xa:fronted:admin:message-pending:${auth.profile?.id}`
  const [form, setForm] = useState<Row>({
    audience: "CUSTOM",
    type: "SYSTEM",
    title: "",
    preview: "",
    detail: "",
    recipient_account_ids: [],
  })
  const [reviewed, setReviewed] = useState(false)
  const [unknown, setUnknown] = useState(() => !!sessionStorage.getItem(marker))
  const [clearReviewed, setClearReviewed] = useState(false)
  return (
    <MutationDialog
      contentClassName="sm:max-w-5xl"
      title="发送站内消息"
      description="消息会发送到所选真实业务账号。请核对标题、正文与接收范围。"
      onClose={onClose}
      submitText="确认发送"
      disabled={
        unknown ||
        !reviewed ||
        !form.title.trim() ||
        (form.audience === "CUSTOM" && !form.recipient_account_ids.length)
      }
      onSubmit={async () => {
        sessionStorage.setItem(
          marker,
          JSON.stringify({
            title: form.title,
            startedAt: new Date().toISOString(),
          })
        )
        try {
          const result = await request({
            url: "/messages/push/",
            method: "POST",
            data: {
              broadcast: form.audience === "ALL",
              audience: form.audience,
              recipient_account_ids:
                form.audience === "CUSTOM" ? form.recipient_account_ids : [],
              type: form.type,
              title: form.title.trim(),
              preview: form.preview.trim(),
              detail: form.detail,
            },
          })
          sessionStorage.removeItem(marker)
          toast.success(`已创建 ${result.data.count ?? ""} 条站内消息记录`)
          onSaved()
        } catch (cause: any) {
          if (cause.status >= 400 && cause.status < 500 && cause.status !== 409)
            sessionStorage.removeItem(marker)
          else setUnknown(true)
          throw cause
        }
      }}
    >
      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1.4fr)_320px]">
        <FormSection title="接收范围与消息内容">
          <div className="grid grid-cols-2 gap-4">
            <Field
              field={{
                key: "message-audience",
                label: "接收范围",
                type: "select",
                options: [
                  ["CUSTOM", "所选业务账号"],
                  ["CUSTOMER", "启用的旧老板账号"],
                  ["PROVIDER", "启用的旧陪玩账号"],
                  ["ALL", "全部启用旧用户（包括后台账号）"],
                ],
              }}
              value={form.audience}
              onChange={(value) => {
                setForm((old) => ({ ...old, audience: value }))
                setReviewed(false)
              }}
            />
            <Field
              field={{
                key: "message-kind",
                label: "消息类型",
                type: "select",
                options: [
                  ["SYSTEM", "系统通知"],
                  ["ORDER", "订单消息"],
                  ["SUPPORT", "客服消息"],
                  ["PROMOTION", "活动通知"],
                ],
              }}
              value={form.type}
              onChange={(value) => {
                setForm((old) => ({ ...old, type: value }))
                setReviewed(false)
              }}
            />
            {form.audience === "CUSTOM" && (
              <div className="col-span-2">
                <OptionPicker
                  resource="message-recipients"
                  label="接收业务账号"
                  multiple
                  value={form.recipient_account_ids}
                  onChange={(value) => {
                    setForm((old) => ({ ...old, recipient_account_ids: value }))
                    setReviewed(false)
                  }}
                />
              </div>
            )}
            {[
              { key: "title", label: "标题", required: true },
              { key: "preview", label: "摘要" },
              {
                key: "detail",
                label: "详情正文",
                type: "textarea",
                full: true,
              },
            ].map((field) => (
              <Field
                key={field.key}
                field={field as any}
                value={form[field.key]}
                onChange={(value) => {
                  setForm((old) => ({ ...old, [field.key]: value }))
                  setReviewed(false)
                }}
              />
            ))}
          </div>
        </FormSection>
        <aside className="rounded-xl border bg-muted/20 p-4 text-sm">
          <p className="mb-4 text-xs text-muted-foreground">通知预览</p>
          <p className="font-semibold">{form.title || "尚未填写标题"}</p>
          <p className="mt-2">{form.preview}</p>
          <p className="mt-3 text-xs text-muted-foreground">
            接收范围：
            {form.audience === "CUSTOM"
              ? `${form.recipient_account_ids.length} 个所选业务账号`
              : form.audience}
          </p>
        </aside>
      </div>
      <label className="flex items-start gap-2 text-sm">
        <input
          type="checkbox"
          className="mt-1 size-4 accent-blue-600"
          checked={reviewed}
          onChange={(event) => setReviewed(event.target.checked)}
        />
        已核对消息内容与接收范围，确认发送
      </label>
      {unknown && (
        <div className="space-y-3">
          <Feedback message="有一笔消息发送结果待核对。请关闭并查询消息记录，核对后再决定是否发送，避免重复通知。" />
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-1 size-4 accent-blue-600"
              checked={clearReviewed}
              onChange={(event) => setClearReviewed(event.target.checked)}
            />
            已核对相关消息记录与接收范围；原请求可能仍会完成，解除后将自行判断是否还需发送
          </label>
          <Button
            type="button"
            variant="outline"
            disabled={!clearReviewed}
            onClick={() => {
              sessionStorage.removeItem(marker)
              setUnknown(false)
              setClearReviewed(false)
            }}
          >
            已核对，解除限制
          </Button>
        </div>
      )}
    </MutationDialog>
  )
}

export function ChatPage() {
  const [params] = useSearchParams()
  const [keyword, setKeyword] = useState("")
  const [unreadOnly, setUnreadOnly] = useState(
    ["1", "true", "True"].includes(params.get("only_unread") || "")
  )
  const list = useList("chat-sessions", {
    keyword,
    only_unread: unreadOnly ? "1" : "",
  })
  const [sessionId, setSessionId] = useState<number | null>(
    Number(params.get("session_id")) || null
  )
  const [sessionInfo, setSessionInfo] = useState<Row | null>(null)
  const [receivers, setReceivers] = useState<Row[]>([])
  const [target, setTarget] = useState("")
  const [assignmentBusy, setAssignmentBusy] = useState(false)
  const [messages, setMessages] = useState<Row[]>([])
  const [draft, setDraft] = useState("")
  const [file, setFile] = useState<File | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [hasOlder, setHasOlder] = useState(false)
  const [nextBefore, setNextBefore] = useState<number | null>(null)
  const [sending, setSending] = useState(false)
  const [abandonOpen, setAbandonOpen] = useState(false)
  const [pending, setPending] = useState<{
    id: string
    content: string
    file: File | null
  } | null>(null)
  const active = useRef(sessionId)
  active.current = sessionId
  const drafts = useRef(new Map<number, string>())
  const pendingBySession = useRef(
    new Map<number, { id: string; content: string; file: File | null }>()
  )
  const owner = useRef(getSession("admin").revision)
  const generation = useRef(0)
  const bottom = useRef<HTMLDivElement>(null)
  const viewport = useRef<HTMLDivElement>(null)
  const browsing = useRef(false)
  const [historyBrowsing, setHistoryBrowsing] = useState(false)
  const auth = useAdminAuth()
  const pendingKey = (id: number) =>
    `xa:store:${getStoreSlug()}:xa:fronted:admin:chat-pending:${auth.profile?.id}:${id}`
  function forgetPending(id: number) {
    try {
      sessionStorage.removeItem(pendingKey(id))
    } catch {}
  }
  useEffect(() => {
    const id = Number(params.get("session_id"))
    if (Number.isSafeInteger(id) && id > 0) setSessionId(id)
  }, [params])
  async function load(older = false) {
    const id = active.current
    if (!id) return
    const version = ++generation.current
    const oldHeight = viewport.current?.scrollHeight || 0
    const oldTop = viewport.current?.scrollTop || 0
    setLoading(true)
    setError("")
    try {
      const info = (await request({ url: `/chat-sessions/${id}/` })).data
      if (version !== generation.current || id !== active.current) return
      setSessionInfo(info)
      if (!info.can_read) {
        setMessages([])
        setDraft("")
        setFile(null)
        setPending(null)
        drafts.current.delete(id)
        setHasOlder(false)
        return
      }
      const result = (
        await request({
          url: `/chat-sessions/${id}/messages/`,
          params: {
            cursor: 1,
            ...(older && nextBefore ? { before_id: nextBefore } : {}),
          },
        })
      ).data
      if (version !== generation.current || id !== active.current) return
      const fetched: Row[] = result.list || []
      setMessages((current) =>
        older || browsing.current
          ? [
              ...new Map(
                [...fetched, ...current].map((row) => [row.id, row])
              ).values(),
            ].sort((a, b) => a.id - b.id)
          : fetched.sort((a, b) => a.id - b.id)
      )
      if (older || !browsing.current) {
        setHasOlder(!!result.has_more)
        setNextBefore(result.next_before_id || null)
      }
      if (
        !older &&
        !browsing.current &&
        !document.hidden &&
        fetched.some((item) => !item.is_from_support) &&
        auth.hasPerm("chat:reply")
      ) {
        await request({
          url: `/chat-sessions/${id}/read/`,
          method: "POST",
          data: {
            message_ids: fetched
              .filter((item) => !item.is_from_support)
              .map((item) => item.id)
              .slice(-200),
          },
        })
        void list.reload()
      }
      if (older)
        requestAnimationFrame(() => {
          if (viewport.current)
            viewport.current.scrollTop =
              oldTop + viewport.current.scrollHeight - oldHeight
        })
      else if (!browsing.current)
        requestAnimationFrame(() =>
          bottom.current?.scrollIntoView({ block: "nearest" })
        )
    } catch (cause: any) {
      if (id === active.current && version === generation.current) {
        if (cause.status === 403 || cause.status === 404) {
          setMessages([])
          setSessionInfo(null)
          setDraft("")
          setFile(null)
        }
        setError(cause.message)
      }
    } finally {
      if (id === active.current && version === generation.current)
        setLoading(false)
    }
  }
  useEffect(() => {
    if (owner.current !== auth.revision) {
      owner.current = auth.revision
      pendingBySession.current.clear()
      drafts.current.clear()
    }
    ++generation.current
    browsing.current = false
    setHistoryBrowsing(false)
    setMessages([])
    setSessionInfo(null)
    setReceivers([])
    setTarget("")
    let recovered = sessionId
      ? pendingBySession.current.get(sessionId) || null
      : null
    if (sessionId && !recovered) {
      try {
        const raw = sessionStorage.getItem(pendingKey(sessionId))
        if (raw) {
          const saved = JSON.parse(raw)
          recovered = {
            id: typeof saved.id === "string" ? saved.id : "",
            content: typeof saved.content === "string" ? saved.content : "",
            file: null,
          }
          pendingBySession.current.set(sessionId, recovered)
        }
      } catch {
        recovered = { id: "", content: "", file: null }
        pendingBySession.current.set(sessionId, recovered)
      }
    }
    setPending(recovered)
    setFile(null)
    setDraft(
      sessionId ? drafts.current.get(sessionId) || recovered?.content || "" : ""
    )
    setHasOlder(false)
    setNextBefore(null)
    if (sessionId) void load()
    return () => {
      ++generation.current
    }
  }, [sessionId, auth.revision])
  useRealtime(
    "admin",
    (event) => {
      if (event.type === "reconnected" || event.type.includes("chat")) {
        void list.reload()
        if (
          active.current &&
          (!event.data?.session_id ||
            Number(event.data.session_id) === active.current)
        )
          void load()
      }
    },
    !!auth.token
  )
  async function send() {
    const id = active.current
    if (
      !auth.hasPerm("chat:reply") ||
      !sessionInfo?.can_read ||
      list.error ||
      !id ||
      sending ||
      pending ||
      (!draft.trim() && !file)
    )
      return
    const intent = { id: requestId("chat"), content: draft.trim(), file }
    const revision = auth.revision
    try {
      sessionStorage.setItem(
        pendingKey(id),
        JSON.stringify({
          id: intent.id,
          content: intent.content,
          hasImage: !!intent.file,
        })
      )
    } catch {
      setError("无法保存发送编号，本次尚未发送。请恢复浏览器存储后重试。")
      return
    }
    pendingBySession.current.set(id, intent)
    setPending(intent)
    setSending(true)
    setError("")
    const body = new FormData()
    body.append("content", intent.content)
    body.append("client_message_id", intent.id)
    body.append("read_through_id", String(messages.at(-1)?.id || 0))
    if (file) body.append("image", file)
    try {
      await request({
        url: `/chat-sessions/${id}/reply/`,
        method: "POST",
        data: body,
      })
      if (getSession("admin").revision !== revision) return
      pendingBySession.current.delete(id)
      forgetPending(id)
      drafts.current.delete(id)
      if (active.current !== id) return
      setPending(null)
      setDraft("")
      setFile(null)
      drafts.current.delete(id)
      await load()
      void list.reload()
    } catch (cause: any) {
      if (getSession("admin").revision !== revision) return
      const rejected =
        cause.status >= 400 &&
        cause.status < 500 &&
        ![408, 409, 429].includes(cause.status)
      if (rejected) {
        pendingBySession.current.delete(id)
        forgetPending(id)
      }
      if (active.current !== id) return
      setError(cause.message || "发送结果未确认，请核对发送状态。")
      if (rejected) setPending(null)
    } finally {
      setSending(false)
    }
  }
  async function checkPending() {
    if (!sessionId || !pending || !pending.id) return
    const revision = auth.revision
    const id = sessionId
    const clientId = pending.id
    setError("")
    try {
      const result = (
        await request({
          url: `/chat-sessions/${sessionId}/message-status/`,
          params: { client_message_id: pending.id },
        })
      ).data
      if (getSession("admin").revision !== revision || active.current !== id)
        return
      if (result.status === "FOUND") {
        if (result.message?.client_message_id !== clientId)
          throw new Error("发送状态与原消息编号不一致，请稍后重试。")
        pendingBySession.current.delete(sessionId)
        forgetPending(sessionId)
        setPending(null)
        setDraft("")
        setFile(null)
        drafts.current.delete(sessionId)
        await load()
        toast.success("已核对，消息发送成功")
      } else
        setError("当前暂未查到这笔消息，原请求可能仍在处理。请稍后再次核对。")
    } catch (cause: any) {
      setError(cause.message)
    }
  }
  async function changeAssignment(action: "claim" | "release" | "transfer") {
    if (!sessionId || !sessionInfo || assignmentBusy) return
    const id = sessionId
    setAssignmentBusy(true)
    setError("")
    try {
      await request({ url: `/chat-sessions/${id}/${action}/`, method: "POST", data: {
        expected_version: sessionInfo.claim_version,
        ...(action === "transfer" ? { account_id: Number(target) } : {}),
      } })
      setReceivers([])
      setTarget("")
      await list.reload()
      if (active.current === id) await load()
    } catch (cause: any) {
      setError(cause.message)
      void list.reload()
      if (active.current === id) void load()
    } finally { setAssignmentBusy(false) }
  }
  async function chooseReceiver() {
    try {
      setReceivers((await request({ url: "/chat-sessions/receivers/" })).data || [])
    } catch (cause: any) { setError(cause.message) }
  }
  const selectedSession = sessionInfo || list.rows.find(row => row.id === sessionId)
  const mayManage = !!sessionInfo && (sessionInfo.claimed_by === auth.profile?.id || auth.hasPerm("chat:supervise"))
  return (
    <div className="admin-chat-page mx-auto max-w-[1600px] space-y-4">
      <SupportHeader
        tone="feature"
        tag="共享会话池"
        title="会话接待"
        description="客服领取后接待，主管可查看和分配会话。"
        metrics={[{ label: "当前筛选会话", value: list.data ? list.total : "—" }, { label: "本页待回复", value: list.data ? list.rows.reduce((sum, row) => sum + Number(row.unread_support || 0), 0) : "—" }]}
      >
        <RefreshButton loading={list.loading} reload={list.reload} />
      </SupportHeader>
      <ReadOnlyNotice canEdit={auth.hasPerm("chat:reply")} subject="客服会话" />
      <div
        data-chat-session={sessionId || undefined}
        className="grid h-[calc(100vh-272px)] max-h-[820px] min-h-[620px] grid-cols-[320px_minmax(0,1fr)] overflow-hidden rounded-xl border bg-card shadow-sm"
      >
        <section className="flex min-h-0 flex-col border-r">
          <div className="space-y-3 border-b p-4">
            <div>
              <h2 className="text-sm font-semibold">站内会话</h2>
              <p className="mt-1 text-[11px] text-muted-foreground">
                按最后消息时间倒序
              </p>
            </div>
            <div className="space-y-2">
            <Label htmlFor="chat-session-keyword">搜索会话</Label>
            <div className="admin-chat-search relative"><Search className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" /><Input
              id="chat-session-keyword"
              data-search-input
              className="h-10 pl-9 pr-9"
              placeholder="输入昵称、账号或手机号…"
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
            />{keyword && <Button type="button" variant="ghost" size="icon" className="absolute top-1 right-1 size-8" onClick={() => setKeyword("")} aria-label="清除会话搜索"><X className="size-3.5" /></Button>}</div>
            </div>
            <Button type="button" variant={unreadOnly ? "secondary" : "outline"} size="sm" className="h-9 w-full justify-start" aria-pressed={unreadOnly} onClick={() => setUnreadOnly(value => !value)}><MessageCircleMore className="size-4" />仅看未读会话</Button>
          </div>
          <Feedback message={list.error} retry={list.reload} />
          <div
            className={`min-h-0 flex-1 overflow-y-auto ${list.loading && !list.data ? "" : "space-y-1 p-2"}`}
          >
            {list.loading && !list.data && (
              <AdminPageSkeleton page="chat-sessions" />
            )}
            {!list.loading && !list.error && !list.rows.length && (
              <div className="p-6 text-center">
                <h3 className="text-sm font-semibold">没有匹配的会话</h3>
                <p className="mt-2 text-xs text-muted-foreground">
                  调整搜索条件后重试。
                </p>
                <Button
                  className="mt-4"
                  size="sm"
                  variant="outline"
                  onClick={() => setKeyword("")}
                >
                  清除搜索
                </Button>
              </div>
            )}
            {list.rows.map((row) => (
              <button
                key={row.id}
                className={`relative flex min-h-[82px] w-full flex-col justify-center gap-1 rounded-lg border border-transparent p-3 pl-16 text-left transition-colors hover:bg-accent/60 active:bg-accent ${sessionId === row.id ? "border-primary/20 bg-accent text-accent-foreground" : ""}`}
                onClick={() => {
                  if (sessionId) drafts.current.set(sessionId, draft)
                  setSessionId(row.id)
                }}
              >
                <Avatar className="absolute top-3 left-3 size-10 border"><AvatarImage src={row.user_avatar || undefined} /><AvatarFallback className="bg-primary/10 text-xs font-semibold text-primary">
                  {String(
                    row.user_name || row.nickname || row.username || "客"
                  ).slice(0, 1)}
                </AvatarFallback></Avatar>
                <div className="flex items-center justify-between gap-2">
                  <strong className="text-sm">
                    {row.user_name ||
                      row.nickname ||
                      row.username ||
                      `会话 #${row.id}`}
                  </strong>
                  {row.unread_support > 0 && (
                    <span className="rounded-full bg-primary px-2 text-xs text-primary-foreground">
                      {row.unread_support}
                    </span>
                  )}
                </div>
                <p className="mt-2 truncate text-xs text-muted-foreground">
                  {row.last_message || (row.claimed_by ? `接待中：${row.claimed_by_name}` : "待领取")}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {formatDateTime(row.last_message_at)}
                </p>
              </button>
            ))}
          </div>
          <div className="flex items-center justify-between border-t p-3 text-xs">
            <Button
              size="sm"
              variant="ghost"
              disabled={list.page <= 1 || list.loading}
              onClick={() => list.setPage(list.page - 1)}
            >
              上一页
            </Button>
            <span>{list.page}</span>
            <Button
              size="sm"
              variant="ghost"
              disabled={list.page * 20 >= list.total || list.loading}
              onClick={() => list.setPage(list.page + 1)}
            >
              下一页
            </Button>
          </div>
        </section>
        <section className="relative flex min-h-0 min-w-0 flex-col bg-muted/15">
          {sessionId ? (
            <>
              <header className="flex min-h-[73px] shrink-0 flex-wrap items-center gap-3 border-b bg-card px-4 py-3"><Avatar className="size-10 border"><AvatarImage src={selectedSession?.user_avatar || undefined} /><AvatarFallback className="bg-primary/10 text-sm font-semibold text-primary">{String(selectedSession?.user_name || "客").slice(0,1)}</AvatarFallback></Avatar><div className="min-w-0 flex-1"><h2 className="truncate text-sm font-semibold">{selectedSession?.user_name || `会话 #${sessionId}`}</h2><p className="mt-1 text-xs text-muted-foreground">{selectedSession?.user_role_display || "站内咨询"} · {selectedSession?.claimed_by ? `接待：${selectedSession.claimed_by_name}` : "待领取"}</p></div>{sessionInfo && !sessionInfo.claimed_by && auth.hasPerm("chat:reply") && <Button size="sm" disabled={assignmentBusy} onClick={() => void changeAssignment("claim")}>领取会话</Button>}{mayManage && sessionInfo?.claimed_by && <Button size="sm" variant="outline" disabled={assignmentBusy} onClick={() => void changeAssignment("release")}>交还</Button>}{mayManage && <Button size="sm" variant="outline" disabled={assignmentBusy} onClick={() => void chooseReceiver()}>转交／分配</Button>}<Button size="sm" variant="outline" onClick={() => void load()} disabled={loading}><RefreshCw className={"size-4 " + (loading ? "animate-spin" : "")} />重新读取</Button></header>
              {!!receivers.length && <div className="flex items-center gap-2 border-b p-3"><select autoComplete="off" name="选择接待客服" aria-label="选择接待客服" className="h-9 rounded border bg-background px-2 text-sm" value={target} onChange={event => setTarget(event.target.value)}><option value="">选择接待客服</option>{receivers.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select><Button size="sm" disabled={!target || assignmentBusy} onClick={() => void changeAssignment("transfer")}>确认分配</Button></div>}
              <Feedback message={error} retry={() => void load()} />
              {sessionInfo && !sessionInfo.can_read && <p className="p-5 text-sm text-muted-foreground">{sessionInfo.claimed_by ? "该会话由其他客服接待，聊天内容仅接待人和主管可查看。" : "请先领取会话，再查看和回复。"}</p>}
              <div
                ref={viewport}
                onScroll={(event) => {
                  const node = event.currentTarget
                  const next =
                    node.scrollHeight - node.scrollTop - node.clientHeight > 80
                  const was = browsing.current
                  browsing.current = next
                  setHistoryBrowsing(next)
                  if (was && !next && !loading) void load()
                }}
                className={`min-h-0 flex-1 overflow-y-auto ${loading && !messages.length ? "" : "space-y-5 p-5"}`}
              >
                {hasOlder && (
                  <div className="text-center">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={loading}
                      onClick={() => void load(true)}
                    >
                      {loading ? "正在读取…" : "读取更早消息"}
                    </Button>
                  </div>
                )}
                {loading && !messages.length && (
                  <AdminPageSkeleton page="chat-messages" />
                )}
                {messages.map((message) => (
                  <div
                    key={message.id}
                    className={`flex gap-2 ${message.is_from_support ? "flex-row-reverse" : ""}`}
                  >
                    <span
                      className={`grid size-8 shrink-0 place-items-center rounded-full border text-[11px] font-semibold ${message.is_from_support ? "bg-primary text-primary-foreground" : "bg-card"}`}
                    >
                      {message.is_from_support ? (
                        <Headset className="size-4" />
                      ) : (
                        String(message.sender_name || "客").slice(0, 1)
                      )}
                    </span>
                    <div className="max-w-[75%] space-y-1">
                      <p className="text-xs text-muted-foreground">
                        {message.sender_name ||
                          (message.is_from_support ? "客服" : "咨询人")}{" "}
                        · {formatDateTime(message.created_at)}
                      </p>
                      {message.content && (
                        <p
                          className={`rounded-xl border px-3 py-2 text-sm leading-6 break-words whitespace-pre-wrap shadow-sm ${message.is_from_support ? "rounded-tr-sm border-primary/20 bg-primary text-primary-foreground" : "rounded-tl-sm bg-card"}`}
                        >
                          {String(message.content)
                            .split(/(\bORD[0-9A-F]{20}\b)/gi)
                            .map((part, index) =>
                              /^ORD[0-9A-F]{20}$/i.test(part) &&
                              auth.hasPerm("order:view") ? (
                                <Link
                                  key={index}
                                  to={`/orders?order_no_exact=${part.toUpperCase()}`}
                                  className="inline-flex items-center gap-1 underline decoration-current/40 underline-offset-2"
                                >
                                  {part}
                                  <ExternalLink className="size-3" />
                                </Link>
                              ) : (
                                part
                              )
                            )}
                        </p>
                      )}
                      {message.order_card && (message.order_card.available ? <Link to={`/orders?order_no_exact=${encodeURIComponent(message.order_card.order_no)}`} className="block rounded-lg border bg-card p-2 text-sm underline">订单 {message.order_card.order_no} · {message.order_card.service_name}<br />发送时：<Status value={message.order_card.status} label={message.order_card.status_display} /> · 查看订单和售后</Link> : <p className="rounded-lg border p-2 text-xs">关联订单当前不可查看</p>)}
                      {sessionInfo?.id === sessionId && sessionInfo?.can_read === true && Number.isSafeInteger(sessionInfo.claimed_by) && sessionInfo.claimed_by > 0 && (sessionInfo.claimed_by === auth.profile?.id || auth.hasPerm("chat:supervise")) && message.is_from_support === false && message.order_card?.available === true && auth.hasPerm("order:view") && auth.hasPerm("support:edit") && <ConsultationAddonCase key={`${auth.revision}:${sessionId}:${message.id}`} sessionId={sessionId} messageId={message.id} orderId={message.order_card.id} />}
                      {message.image_url && (
                        <PrivateImage
                          label="聊天图片"
                          urls={[message.image_url]}
                        />
                      )}
                    </div>
                  </div>
                ))}
                <div ref={bottom} />
              </div>
              {historyBrowsing && (
                <div className="flex justify-center pb-3">
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      browsing.current = false
                      setHistoryBrowsing(false)
                      bottom.current?.scrollIntoView({ block: "nearest" })
                      void load()
                    }}
                  >
                    回到最新消息
                  </Button>
                </div>
              )}
              {sessionInfo?.can_read && <footer className="space-y-3 border-t bg-card p-4">
                {pending && (
                  <div className="flex items-center justify-between gap-3 rounded-lg border bg-muted/40 p-3 text-xs">
                    <span>
                      {pending.id
                        ? "发送结果待核对，草稿已保留。"
                        : "本地记录缺少发送编号，请先查看当前消息再解除限制。"}
                    </span>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={checkPending}
                      disabled={sending || !pending.id}
                    >
                      核对发送状态
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={sending}
                      onClick={() => setAbandonOpen(true)}
                    >
                      放弃本地核对
                    </Button>
                  </div>
                )}
                <Textarea
                  aria-label="客服回复内容"
                  placeholder="输入回复内容，例如已为您核实…"
                  value={draft}
                  disabled={
                    !auth.hasPerm("chat:reply") ||
                    !!list.error ||
                    sending ||
                    !!pending
                  }
                  maxLength={1000}
                  onChange={(event) => {
                    setDraft(event.target.value)
                    drafts.current.set(sessionId, event.target.value)
                  }}
                />
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <label className={"inline-flex min-h-9 cursor-pointer items-center gap-2 rounded-lg border px-3 text-sm transition-colors hover:bg-accent " + (!auth.hasPerm("chat:reply") || !!list.error || sending || !!pending ? "pointer-events-none opacity-50" : "")}><ImagePlus className="size-4" />附加图片<Input
                    aria-label="附加聊天图片"
                    className="sr-only"
                    type="file"
                    accept="image/*"
                    disabled={
                      !auth.hasPerm("chat:reply") ||
                      !!list.error ||
                      sending ||
                      !!pending
                    }
                    onChange={(event) =>
                      setFile(event.target.files?.[0] || null)
                    }
                  /></label>
                  {file && <span className="max-w-64 truncate text-xs text-muted-foreground" title={file.name}>{file.name}</span>}
                  <Button
                    aria-busy={sending}
                    onClick={send}
                    disabled={
                      !auth.hasPerm("chat:reply") ||
                      !!list.error ||
                      sending ||
                      !!pending ||
                      (!draft.trim() && !file)
                    }
                  >
                    {sending ? <LoaderCircle className="size-4 animate-spin" /> : <Send className="size-4" />}{sending ? "正在发送" : "发送回复"}
                  </Button>
                </div>
              </footer>}
            </>
          ) : (
            <div className="grid flex-1 place-items-center p-8 text-center"><div><MessageCircleMore className="mx-auto size-9 text-muted-foreground" /><h2 className="mt-4 text-base font-semibold">选择一条会话开始接待</h2><p className="mt-2 text-xs text-muted-foreground">当前会话的草稿会单独保留</p></div></div>
          )}
        </section>
      </div>
      {abandonOpen && pending && sessionId && (
        <MutationDialog
          title="放弃本地发送核对？"
          description="原请求可能仍在处理中。请先查看当前会话，确认后仅解除本地限制，不会撤销或重发消息。"
          submitText="已核对，放弃本地记录"
          submitVariant="destructive"
          onClose={() => setAbandonOpen(false)}
          onSubmit={async () => {
            forgetPending(sessionId)
            pendingBySession.current.delete(sessionId)
            setPending(null)
            setAbandonOpen(false)
            setFile(null)
            await load()
          }}
        />
      )}
    </div>
  )
}

export function AuditionsPage({ signups = false }: { signups?: boolean }) {
  const auth = useAdminAuth()
  const [keyword, setKeyword] = useState("")
  const [query, setQuery] = useState<Row>({
    keyword: "",
    status: "",
    is_active: "",
    link: "",
  })
  const list = useList(signups ? "audition-signups" : "audition-links", query)
  const [editing, setEditing] = useState<Row | null>(null)
  const [operation, setOperation] = useState<{ kind: string; row: Row } | null>(
    null
  )
  const locked = list.loading || !!list.error
  const clearFilters = () => {
    setKeyword("")
    setQuery({ keyword: "", status: "", is_active: "", link: "" })
  }
  const binding = (row: Row, role: "boss" | "provider") => {
    const current = row[`${role}_binding`]
    const Icon = role === "boss" ? UserRound : UsersRound
    return (
      <div>
        <div className="flex items-center gap-2">
          <Icon className="size-4 shrink-0 text-primary" />
          <strong className="text-xs font-medium">
            {current?.name || row[`${role}_user_name`] || (current === undefined ? <Status readState="unread" /> : "未绑定")}
          </strong>
        </div>
        {current?.name && (
          <p className="mt-1 text-[11px] text-muted-foreground">
            {[current.account_no, current.username].filter(Boolean).join(" · ")}
          </p>
        )}
        {current?.kind === "legacy" && (
          <p className="mt-1 text-[11px] text-warning">历史绑定，需核对账号</p>
        )}
        {current?.kind === "account" && current.available === false && (
          <p className="mt-1 text-[11px] text-warning">绑定账号当前不可登录</p>
        )}
        {current?.otp_required && (
          <p className="mt-1 text-[11px] text-muted-foreground">
            需账号和验证器登录
          </p>
        )}
        {row[`${role}_url_placeholder`] && (
          <p className="mt-1 text-[11px] text-warning">入口域名待配置</p>
        )}
      </div>
    )
  }
  return (
    <DataPageState
      loading={list.loading}
      hasSnapshot={!!list.data}
      error={list.error}
      retry={list.reload}
      skeleton={signups ? "audition-signups" : "audition-links"}
    >
      <div className="space-y-4">
        <SupportHeader
          tone="feature"
          title={signups ? "试音报名审核" : "试音招募"}
          metrics={[
            {
              label: signups ? "符合条件报名" : "符合条件活动",
              value: list.total,
            },
            {
              label: signups ? "本页待审核" : "本页有效入口",
              value: list.rows.filter((row) =>
                signups
                  ? row.status === "PENDING"
                  : row.entry_status === "ACTIVE"
              ).length,
            },
          ]}
        >
          <RefreshButton loading={list.loading} reload={list.reload} />
          {!signups && auth.hasPerm("audition:edit") && (
            <Button
              size="sm"
              variant="secondary"
              disabled={locked}
              onClick={() =>
                setEditing({
                  title: "",
                  remark: "",
                  expire_at: "",
                  is_active: true,
                })
              }
            >
              创建招募
            </Button>
          )}
        </SupportHeader>
        <ReadOnlyNotice
          canEdit={
            signups
              ? auth.hasPerm("audition:signup_audit")
              : [
                  "audition:edit",
                  "audition:delete",
                  "audition:bind_boss",
                  "audition:bind_provider",
                ].some(auth.hasPerm)
          }
          subject={signups ? "试音报名" : "试音招募"}
        />
        <Feedback message={list.error} retry={list.reload} />
        <DataTable snapshotAvailable={!!list.data}
          title={signups ? "试音报名记录" : "试音活动与双入口"}
          titleIcon={Headset}
          description={`本页 ${list.rows.length} 项 · 按创建时间倒序`}
          minWidth={signups ? 960 : 1080}
          rows={list.rows}
          error={list.error}
          retry={list.reload}
          loading={list.loading}
          empty={
            signups
              ? "没有符合筛选条件的试音报名"
              : "没有符合筛选条件的试音活动"
          }
          emptyDescription="清除关键词与状态筛选后查看全部记录。"
          emptyIcon={signups ? ClipboardCheck : Link2}
          emptyAction={
            <Button variant="outline" onClick={clearFilters}>
              清除筛选
            </Button>
          }
          toolbar={
            <form
              onSubmit={(event) => {
                event.preventDefault()
                setQuery((old) => ({ ...old, keyword: keyword.trim() }))
              }}
            >
              <AdminFilters activeCount={Number(!!query.link)} primary={<>
              <div className="min-w-56 flex-1 space-y-2">
                <Label htmlFor="audition-keyword">
                  {signups ? "搜索报名" : "搜索活动"}
                </Label>
                <div className="relative">
                  <Search className="absolute top-3 left-3 size-4 text-muted-foreground" />
                  <Input
                    id="audition-keyword"
                    className="pl-9"
                    value={keyword}
                    placeholder={
                      signups
                        ? "账号编号、昵称、游戏或联系方式"
                        : "活动标题或备注"
                    }
                    onChange={(event) => {
                      setKeyword(event.target.value)
                      if (!event.target.value)
                        setQuery((old) => ({ ...old, keyword: "" }))
                    }}
                  />
                </div>
              </div>
              <div className="w-36">
                <Field
                  field={
                    signups
                      ? {
                          key: "audition-status",
                          label: "审核状态",
                          type: "select",
                          options: [
                            ["PENDING", "待审核"],
                            ["APPROVED", "已通过"],
                            ["REJECTED", "已驳回"],
                          ],
                        }
                      : {
                          key: "audition-status",
                          label: "启用状态",
                          type: "select",
                          options: [
                            ["true", "启用"],
                            ["false", "停用"],
                          ],
                        }
                  }
                  value={signups ? query.status : query.is_active}
                  onChange={(value) =>
                    setQuery((old) => ({
                      ...old,
                      [signups ? "status" : "is_active"]: value,
                    }))
                  }
                />
              </div>
              </>} actions={<>
              <Button type="submit" variant="outline">
                查询
              </Button>
              <Button type="button" variant="ghost" onClick={clearFilters}>清除筛选</Button>
              </>}>
              {signups ? (auth.hasPerm("audition:view") ? (
                <OptionPicker label="试音活动" resource="audition-links" endpoint="/audition-links/" value={query.link} onChange={(value) => setQuery((old) => ({ ...old, link: value || "" }))} />
              ) : (
                <Field field={{ key: "audition-link", label: "活动编号", type: "number", min: 1 }} value={query.link} onChange={(value) => setQuery((old) => ({ ...old, link: value }))} />
              )) : undefined}
              </AdminFilters>
            </form>
          }
          columns={
            signups
              ? [
                  {
                    key: "link_title",
                    label: "活动 / 报名编号",
                    render: (row) => (
                      <div className="flex items-start gap-3">
                        <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary">
                          <FileText className="size-4" />
                        </span>
                        <div>
                          <p className="text-sm font-medium">
                            {row.link_title || "—"}
                          </p>
                          <p className="mt-1 text-[10px] text-muted-foreground">
                            报名 #{row.id} · 活动 #{row.link}
                          </p>
                        </div>
                      </div>
                    ),
                  },
                  {
                    key: "applicant_name",
                    label: "报名人 / 账号",
                    render: (row) => (
                      <div>
                        <p className="flex items-center gap-2 font-medium">
                          <UserRound className="size-4 shrink-0 text-primary" />
                          {row.applicant_name || "—"}
                        </p>
                        <p className="mt-1 text-[11px] text-muted-foreground">
                          {[row.applicant_no, row.applicant_username]
                            .filter(Boolean)
                            .join(" · ") || "—"}
                        </p>
                      </div>
                    ),
                  },
                  {
                    key: "game",
                    label: "游戏 / 联系方式",
                    render: (row) => (
                      <div>
                        <p className="flex items-center gap-2 text-xs">
                          <Gamepad2 className="size-4 shrink-0 text-primary" />
                          {row.game || "—"}
                        </p>
                        <p className="mt-1 text-[11px] text-muted-foreground">
                          联系 {row.contact || "—"}
                        </p>
                        {row.remark && (
                          <p className="mt-2 text-xs leading-5 text-muted-foreground">
                            {row.remark}
                          </p>
                        )}
                      </div>
                    ),
                  },
                  {
                    key: "status",
                    label: "审核 / 提交时间",
                    align: "center",
                    render: (row) => (
                      <div>
                        <Status
                          value={row.status}
                          label={row.status_display}
                          tone={
                            row.status === "APPROVED"
                              ? "success"
                              : row.status === "REJECTED"
                                ? "destructive"
                                : "primary"
                          }
                        />
                        <p className="mt-2 flex items-center justify-center gap-2 text-[11px] whitespace-nowrap text-muted-foreground">
                          <CalendarClock className="size-3.5 shrink-0" />
                          {formatDateTime(row.created_at)}
                        </p>
                        {row.audit_remark && (
                          <p className="mt-2 text-xs leading-5 text-muted-foreground">
                            {row.audit_remark}
                          </p>
                        )}
                      </div>
                    ),
                  },
                  {
                    key: "actions",
                    label: "操作",
                    render: (row) => (
                      <div className="flex justify-end gap-2">
                        <Button
                          actionTone="read"
                          size="sm"
                          variant="outline"
                          onClick={() => setOperation({ kind: "detail", row })}
                        >
                          详情
                        </Button>
                        {row.status === "PENDING" &&
                          auth.hasPerm("audition:signup_audit") && (
                            <>
                              <Button
                                actionTone="approve"
                                size="sm"
                                variant="default"
                                onClick={() =>
                                  setOperation({ kind: "approve", row })
                                }
                              >
                                通过
                              </Button>
                              <Button
                                actionTone="reject"
                                size="sm"
                                variant="ghost"
                                className="text-destructive hover:text-destructive"
                                onClick={() =>
                                  setOperation({ kind: "reject", row })
                                }
                              >
                                驳回
                              </Button>
                            </>
                          )}
                      </div>
                    ),
                  },
                ]
              : [
                  {
                    key: "title",
                    label: "试音活动",
                    render: (row) => (
                      <div className="flex items-start gap-3">
                        <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary">
                          <Headset className="size-5" />
                        </span>
                        <div>
                          <p className="text-sm font-medium">{row.title}</p>
                          <p className="mt-1 text-[10px] text-muted-foreground">
                            活动 #{row.id} · {formatDateTime(row.created_at)}
                          </p>
                          {row.remark && (
                            <p className="mt-1 text-xs leading-5 text-muted-foreground">
                              {row.remark}
                            </p>
                          )}
                        </div>
                      </div>
                    ),
                  },
                  {
                    key: "boss_binding",
                    label: "老板入口",
                    render: (row) => binding(row, "boss"),
                  },
                  {
                    key: "provider_binding",
                    label: "陪玩入口",
                    render: (row) => binding(row, "provider"),
                  },
                  {
                    key: "entry_status",
                    label: "状态 / 有效期",
                    align: "center",
                    render: (row) => (
                      <div>
                        <Status
                          value={row.entry_status ?? row.is_active}
                          tone={
                            row.entry_status === "ACTIVE"
                              ? "success"
                              : row.entry_status === "EXPIRED"
                                ? "warning"
                                : "muted"
                          }
                          label={
                            row.entry_status ? ({ ACTIVE: "启用", EXPIRED: "已过期", DISABLED: "停用" } as Record<string, string>)[row.entry_status] : row.is_active == null ? undefined : row.is_active ? "启用" : "停用"
                          }
                        />
                        <p className="mt-2 flex items-center justify-center gap-2 text-[11px] whitespace-nowrap text-muted-foreground">
                          <CalendarClock className="size-3.5 shrink-0" />
                          {row.expire_at
                            ? formatDateTime(row.expire_at)
                            : row.expire_at === null ? "长期有效" : "—"}
                        </p>
                      </div>
                    ),
                  },
                  {
                    key: "actions",
                    label: "操作",
                    render: (row) => (
                      <div className="flex flex-wrap justify-end gap-2">
                        {auth.hasPerm("audition:edit") && (
                          <>
                            <Button
                              actionTone="edit"
                              size="sm"
                              variant="outline"
                              onClick={() => setEditing(row)}
                            >
                              编辑
                            </Button>
                          </>
                        )}
                        {auth.hasPerm("audition:bind_boss") && (
                          <>
                            <Button
                              actionTone="edit"
                              size="sm"
                              variant="ghost"
                              onClick={() =>
                                setOperation({ kind: "bind-boss", row })
                              }
                            >
                              绑定老板
                            </Button>
                          </>
                        )}
                        {auth.hasPerm("audition:bind_provider") && (
                          <>
                            <Button
                              actionTone="edit"
                              size="sm"
                              variant="ghost"
                              onClick={() =>
                                setOperation({ kind: "bind-provider", row })
                              }
                            >
                              绑定陪玩
                            </Button>
                          </>
                        )}
                        <Button
                          actionTone="read"
                          size="sm"
                          variant="ghost"
                          onClick={() => setOperation({ kind: "links", row })}
                        >
                          查看入口
                        </Button>
                        {auth.hasPerm("audition:delete") && (
                          <Button
                            actionTone="reject"
                            size="sm"
                            variant="ghost"
                            className="text-destructive hover:text-destructive"
                            onClick={() =>
                              setOperation({ kind: "delete", row })
                            }
                          >
                            删除
                          </Button>
                        )}
                      </div>
                    ),
                  },
                ]
          }
        />
        <Pagination snapshotAvailable={!!list.data}
          page={list.page}
          total={list.total}
          loading={list.loading}
          onChange={list.setPage}
          pageSize={list.pageSize}
          onPageSizeChange={list.setPageSize}
        />
        {editing && (
          <AuditionEditor
            row={editing}
            locked={locked}
            onClose={() => setEditing(null)}
            onSaved={list.reload}
          />
        )}
        {operation && (
          <AuditionAction
            operation={operation}
            onClose={() => setOperation(null)}
            onSaved={list.reload}
          />
        )}
      </div>
    </DataPageState>
  )
}
function AuditionEditor({
  row,
  onClose,
  onSaved,
  locked,
}: {
  row: Row
  locked: boolean
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState<Row>({
    ...row,
    expire_at: row.expire_at
      ? new Date(
          new Date(row.expire_at).valueOf() -
            new Date(row.expire_at).getTimezoneOffset() * 60000
        )
          .toISOString()
          .slice(0, 16)
      : "",
  })
  return (
    <MutationDialog
      title={row.id ? "编辑招募" : "创建试音招募"}
      submitText="保存活动"
      description="设置标题、说明、有效期与启用状态。"
      onClose={onClose}
      contentClassName="admin-audition-editor sm:max-w-2xl"
      confirmDiscard
      readOnly={locked}
      disabled={!form.title.trim()}
      onSubmit={async () => {
        await request({
          url: `/audition-links/${row.id ? `${row.id}/` : ""}`,
          method: row.id ? "PATCH" : "POST",
          data: {
            title: form.title,
            remark: form.remark,
            expire_at: form.expire_at
              ? new Date(form.expire_at).toISOString()
              : null,
            is_active: form.is_active,
          },
        })
        toast.success("招募已保存")
        onSaved()
      }}
    >
      <div className="grid gap-4 md:grid-cols-2">
        <div className="md:col-span-2">
          <Field
            field={{
              key: "title",
              label: "活动标题",
              required: true,
              maxLength: 100,
            }}
            value={form.title}
            onChange={(value) => setForm((old) => ({ ...old, title: value }))}
          />
        </div>
        <div className="md:col-span-2">
          <Field
            field={{
              key: "remark",
              label: "备注",
              type: "textarea",
              maxLength: 255,
            }}
            value={form.remark}
            onChange={(value) => setForm((old) => ({ ...old, remark: value }))}
          />
        </div>
        <div>
          <Field
            field={{
              key: "expire_at",
              label: "截止时间",
              type: "datetime-local",
            }}
            value={form.expire_at}
            onChange={(value) =>
              setForm((old) => ({ ...old, expire_at: value }))
            }
          />
          <p className="mt-2 text-[11px] text-muted-foreground">
            留空为长期有效；过期后入口不可使用。
          </p>
        </div>
        <label className="flex min-h-11 items-center justify-between gap-3 self-start rounded-xl border px-3">
          <span>
            <span className="block text-xs font-medium">启用活动</span>
            <span className="mt-1 block text-[11px] text-muted-foreground">
              停用后两个入口均不可使用
            </span>
          </span>
          <Switch name="启用活动"
            aria-label="启用活动"
            checked={!!form.is_active}
            onCheckedChange={(value) =>
              setForm((old) => ({ ...old, is_active: value }))
            }
          />
        </label>
      </div>
    </MutationDialog>
  )
}
function AuditionAction({
  operation,
  onClose,
  onSaved,
}: {
  operation: { kind: string; row: Row }
  onClose: () => void
  onSaved: () => void
}) {
  const [account, setAccount] = useState<number | null>(null)
  const [remark, setRemark] = useState("")
  const { kind, row } = operation
  const [unbind, setUnbind] = useState(false)
  const [reviewUnknown, setReviewUnknown] = useState(false)
  const [reviewConflict, setReviewConflict] = useState(false)
  const preReview = useLoad(
    async () =>
      ["approve", "reject"].includes(kind)
        ? (await request({ url: `/audition-signups/${row.id}/` })).data
        : null,
    [row.id, kind]
  )
  if (kind === "detail")
    return <AuditionSignupDetail id={row.id} onClose={onClose} />
  if (kind === "delete")
    return (
      <MutationDialog
        title="删除试音招募"
        submitVariant="destructive"
        description={`确认删除「${row.title}」？有关联报名的招募无法删除，请先停用并保留审核记录。`}
        onClose={onClose}
        onSubmit={async () => {
          await request({ url: `/audition-links/${row.id}/`, method: "DELETE" })
          toast.success("已删除招募")
          onSaved()
        }}
      />
    )
  if (kind === "links")
    return (
      <Dialog
        open
        onOpenChange={(value) => {
          if (!value) onClose()
        }}
      >
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>{row.title} · 招募入口</DialogTitle>
            <DialogDescription>
              入口包含招募身份，请只分享给对应参与者。
            </DialogDescription>
          </DialogHeader>
          {[
            ["boss_url", "老板入口"],
            ["provider_url", "陪玩入口"],
          ].map(([key, label]) => (
            <div key={key} className="space-y-2">
              <Label>{label}</Label>
              {row[key] && !row[`${key}_placeholder`] ? (
                <a
                  className="block text-sm break-all text-primary underline"
                  href={row[key]}
                  target="_blank"
                  rel="noreferrer"
                >
                  {row[key]}
                </a>
              ) : (
                <p className="text-sm text-muted-foreground">
                  {row[`${key}_placeholder`]
                    ? "当前环境尚未配置有效的入口地址，请先配置后复制。"
                    : "请先绑定账号并核对入口状态"}
                </p>
              )}
              {row[key] && !row[`${key}_placeholder`] && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(row[key])
                      toast.success("入口已复制")
                    } catch {
                      toast.error("浏览器未允许复制，请选择入口文本复制。")
                    }
                  }}
                >
                  复制{label}
                </Button>
              )}
            </div>
          ))}
        </DialogContent>
      </Dialog>
    )
  const binding = kind.startsWith("bind-")
  return (
    <MutationDialog
      title={
        binding
          ? kind === "bind-boss"
            ? "绑定老板账号"
            : "绑定陪玩账号"
          : kind === "approve"
            ? "通过试音报名"
            : "驳回试音报名"
      }
      description={
        row.title ||
        `${row.link_title} · ${row.applicant_name} · ${row.applicant_username}`
      }
      onClose={onClose}
      disabled={
        binding
          ? !account && !unbind
          : reviewUnknown ||
            reviewConflict ||
            preReview.loading ||
            !!preReview.error ||
            preReview.data?.status !== "PENDING" ||
            (kind === "reject" && !remark.trim())
      }
      submitVariant={
        kind === "reject" || (binding && unbind) ? "destructive" : "default"
      }
      onSubmit={async () => {
        if (binding) {
          await request({
            url: `/audition-links/${row.id}/${kind}/`,
            method: "POST",
            data: {
              account_id: unbind ? null : account,
              expected_updated_at: row.updated_at,
            },
          })
        } else {
          const current = (
            await request({ url: `/audition-signups/${row.id}/` })
          ).data
          if (current.status !== "PENDING") {
            setReviewConflict(true)
            void preReview.reload()
            throw new Error("报名已处理，请刷新核对。")
          }
          try {
            await request({
              url: `/audition-signups/${row.id}/${kind}/`,
              method: "POST",
              data: kind === "reject" ? { audit_remark: remark.trim() } : {},
            })
          } catch (cause: any) {
            if (cause.status === 409) {
              setReviewConflict(true)
              void preReview.reload()
            } else if (
              !cause.status ||
              cause.status >= 500 ||
              cause.status === 408
            )
              setReviewUnknown(true)
            throw cause
          }
        }
        toast.success("已更新试音记录")
        onSaved()
      }}
    >
      {!binding && (
        <>
          <FormSection title="最新报名资料">
            <DataRegion label="最新报名资料" loading={preReview.loading} hasSnapshot={!!preReview.data} error={preReview.error} retry={preReview.reload}>
            {preReview.data && (
                <Details
                  data={preReview.data}
                  fields={[
                    ["link_title", "试音活动"],
                    ["applicant_name", "报名人"],
                    ["applicant_no", "业务编号"],
                    ["game", "擅长游戏"],
                    ["contact", "联系方式"],
                    ["remark", "报名备注"],
                    ["status_display", "当前状态"],
                  ]}
                />
            )}
            </DataRegion>
          </FormSection>
          {reviewUnknown && (
            <Feedback message="审核结果未确认，请关闭并核对报名状态，避免重复审核。" />
          )}
          {reviewConflict && (
            <Feedback message="报名状态已经变化，本次审核未继续。请关闭后刷新报名列表。" />
          )}
        </>
      )}
      {binding ? (
        <div className="space-y-4">
          <OptionPicker
            resource={
              kind === "bind-boss" ? "audition-bosses" : "audition-providers"
            }
            label={kind === "bind-boss" ? "老板业务账号" : "陪玩业务账号"}
            value={account}
            onChange={setAccount}
            disabled={unbind}
          />
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-1 size-4 accent-blue-600"
              checked={unbind}
              onChange={(event) => setUnbind(event.target.checked)}
            />
            解除现有绑定，并轮换对应入口标识；旧入口将失效
          </label>
        </div>
      ) : (
        <Field
          field={{
            key: "audition-audit-remark",
            label: kind === "reject" ? "驳回原因（必填）" : "审核说明（选填）",
            type: "textarea",
          }}
          value={remark}
          onChange={setRemark}
        />
      )}
    </MutationDialog>
  )
}

function AuditionSignupDetail({
  id,
  onClose,
}: {
  id: number
  onClose: () => void
}) {
  const result = useLoad(
    async () => (await request({ url: "/audition-signups/" + id + "/" })).data,
    [id]
  )
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>报名详情 #{id}</DialogTitle>
          <DialogDescription>核对报名资料与审核记录。</DialogDescription>
        </DialogHeader>
        <FormSection title="报名与审核快照" className="bg-muted/20">
          <DataRegion label="报名与审核快照" loading={result.loading} hasSnapshot={!!result.data} error={result.error} retry={result.reload}>
          {result.data && (
              <Details
                data={result.data}
                fields={[
                  ["link_title", "试音活动"],
                  ["applicant_name", "报名人"],
                  ["applicant_username", "账号"],
                  ["applicant_no", "业务编号"],
                  ["game", "擅长游戏"],
                  ["contact", "联系方式"],
                  ["remark", "报名备注"],
                  ["status_display", "审核状态"],
                  ["auditor_name", "审核人"],
                  ["audit_remark", "审核意见"],
                  ["audited_at", "审核时间"],
                  ["created_at", "报名时间"],
                ]}
              />
          )}
          </DataRegion>
        </FormSection>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            关闭
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
