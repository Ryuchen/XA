import { readChatPending, saveChatPending, clearChatPending } from "@xa/shared"
import {
  providerMessageDestination,
  deliverChatMessage,
  lookupChatMessage,
  mayMarkMessagesRead,
} from "@xa/shared"
import {
  providerChatScope,
  readChatDraft,
  saveChatDraft,
  clearChatDraft,
  removeProviderChatContext,
} from "@xa/shared"
import { useEffect, useRef, useState } from "react"
import { Link, useNavigate, useSearchParams } from "react-router"
import {
  Bell,
  CheckCheck,
  ChevronRight,
  Headphones,
  HelpCircle,
  Send,
  ShieldCheck,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import {
  Panel,
  PageHeader,
  Notice,
  LoadState,
  SnapshotGate,
  EmptyState,
  Confirm,
  PrivateImage,
  UploadField,
} from "@/components/provider-ui"
import {
  api,
  useLoad,
  useRefresh,
  date,
  message,
  requestId,
} from "@/lib/provider"

type SiteMessage = {
  id: number
  type: string
  title: string
  preview: string
  detail?: string
  action_url: string
  related_order_id: number | null
  is_read: boolean
  created_at: string
}
type MessagePage = {
  messages: SiteMessage[]
  unread: number
  next_before: number | null
}
const messageTypeLabel = (type: string) =>
  ({
    SYSTEM: "系统通知",
    ORDER: "订单消息",
    SUPPORT: "客服消息",
    PROMOTION: "活动通知",
  })[type] || "平台通知"
export function MessagesPage() {
  const detailSequence = useRef(0)
  useEffect(
    () => () => {
      detailSequence.current++
    },
    []
  )
  const navigate = useNavigate(),
    [before, setBefore] = useState<number | null>(null),
    [rows, setRows] = useState<SiteMessage[]>([]),
    [detail, setDetail] = useState<SiteMessage | null>(null),
    [target, setTarget] = useState<SiteMessage | null>(null),
    [detailError, setDetailError] = useState(""),
    [detailLoading, setDetailLoading] = useState(false),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false)
  const data = useLoad(async () => {
      const first = await api<MessagePage>(
        `/messages/?page=1&page_size=50${before ? `&before=${before}` : ""}`
      )
      if (before || !rows.length) return first
      const oldest = rows[rows.length - 1].id
      const messages = [...first.messages]
      let next = first.next_before
      while (next && !messages.some((row) => row.id === oldest)) {
        const page = await api<MessagePage>(
          `/messages/?page=1&page_size=50&before=${next}`
        )
        messages.push(...page.messages)
        if (next === page.next_before) break
        next = page.next_before
      }
      return {
        ...first,
        messages: [...new Map(messages.map((row) => [row.id, row])).values()],
        next_before: next,
      }
    }, [before]),
    chat = useLoad(() => api<{ unread: number }>("/chat/unread/"))
  useEffect(() => {
    if (data.data)
      setRows((old) =>
        before
          ? [
              ...new Map(
                [...old, ...data.data!.messages].map((row) => [row.id, row])
              ).values(),
            ]
          : data.data!.messages
      )
  }, [data.data])
  useRefresh(async () => {
    setBefore(null)
    await Promise.all([data.reload(), chat.reload()])
  }, ["site_message_update", "chat_message"])
  async function readAll() {
    setBusy(true)
    try {
      await api("/messages/read-all/", { method: "POST" })
      setNotice("已全部标记为已读")
      await data.reload()
    } catch (cause) {
      setNotice(message(cause))
    } finally {
      setBusy(false)
    }
  }
  async function show(item: SiteMessage) {
    const sequence = ++detailSequence.current
    setTarget(item)
    setDetail(null)
    setDetailError("")
    setDetailLoading(true)
    try {
      const value = await api<SiteMessage>(`/messages/${item.id}/`)
      if (sequence !== detailSequence.current) return
      setDetail(value)
      setRows((old) =>
        old.map((row) => (row.id === item.id ? { ...row, is_read: true } : row))
      )
      void data.reload().catch(() => {})
    } catch (cause) {
      if (sequence === detailSequence.current) setDetailError(message(cause))
    } finally {
      if (sequence === detailSequence.current) setDetailLoading(false)
    }
  }
  function related(row: SiteMessage) {
    const destination = providerMessageDestination(row)
    if (destination) {
      setTarget(null)
      setDetail(null)
      navigate(destination)
    }
  }
  const visibleRows = rows.length ? rows : data.data?.messages || []
  return (
    <div className="space-y-6">
      <PageHeader title="消息中心">
        {!!data.data?.unread ? (
          <Button
            variant="outline"
            className="h-11"
            disabled={busy || !data.data?.unread}
            onClick={() => void readAll()}
          >
            <CheckCheck className="size-4" />
            全部已读
          </Button>
        ) : (
          data.data && (
            <Badge variant="outline" className="mt-1">
              均已阅读
            </Badge>
          )
        )}
      </PageHeader>
      <Notice tone={["已全部标记为已读", "已复制"].includes(notice) ? "success" : "info"}>{notice}</Notice>
      <LoadState
        loading={data.loading}
        error={data.error}
        hasData={data.data != null}
        reload={() => data.reload().catch(() => {})}
        empty={!visibleRows.length}
        emptyTitle="暂无消息"
        emptyDescription="订单、服务和资金通知会在这里显示。"
      >
        <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_260px]">
          <section className="overflow-hidden rounded-[12px] border bg-card">
            <div className="border-b p-4">
              <h2 className="text-sm font-semibold">平台通知</h2>
              <p className="mt-1 text-xs text-muted-foreground">
                {data.data
                  ? data.data.unread
                    ? `${data.data.unread} 条未读通知`
                    : "平台通知均已阅读"
                  : "正在读取通知…"}
              </p>
            </div>
            {visibleRows.map((row) => (
              <button
                key={row.id}
                type="button"
                className="flex min-h-24 w-full items-start gap-3 border-b p-4 text-left last:border-0 hover:bg-accent/30"
                onClick={() => void show(row)}
              >
                <span className="relative grid size-10 shrink-0 place-items-center rounded-[8px] bg-primary/10 text-primary">
                  <Bell className="size-4" />
                  {!row.is_read && (
                    <span
                      className="absolute -top-1 -right-1 size-2.5 rounded-full border-2 border-card bg-destructive"
                      aria-label="未读"
                    />
                  )}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
                    <span className="shrink-0">
                      {messageTypeLabel(row.type)}
                    </span>
                    <time className="text-right">{date(row.created_at)}</time>
                  </span>
                  <span className="mt-1 block text-sm font-semibold">
                    {row.title}
                  </span>
                  <span className="mt-1 line-clamp-2 block text-xs leading-5 text-muted-foreground">
                    {row.preview}
                  </span>
                </span>
                <ChevronRight className="mt-3 size-4 shrink-0 text-muted-foreground" />
              </button>
            ))}
            {data.data?.next_before && (
              <div className="border-t p-4">
                <Button
                  variant="outline"
                  disabled={data.loading}
                  onClick={() => setBefore(data.data!.next_before)}
                >
                  加载更早消息
                </Button>
              </div>
            )}
          </section>
          <aside className="rounded-[12px] border bg-card p-4">
            <span className="grid size-11 place-items-center rounded-[8px] bg-primary/10 text-primary">
              <Headphones className="size-5" />
            </span>
            <h2 className="mt-4 text-sm font-semibold">平台客服</h2>
            <p className="mt-2 text-xs leading-5 text-muted-foreground">
              {chat.error
                ? "未读数暂不可用"
                : chat.data
                  ? `${chat.data.unread} 条未读咨询回复`
                  : "正在读取未读消息"}
            </p>
            <p className="mt-2 text-xs leading-5 text-muted-foreground">
              订单或资金问题可在会话中附上订单号，便于客服核对。
            </p>
            <Button variant="outline" className="mt-4 h-11 w-full" asChild>
              <Link to="/support-chat">进入客服会话</Link>
            </Button>
          </aside>
        </div>
      </LoadState>
      <Dialog
        open={!!target}
        onOpenChange={(open) => {
          if (!open) {
            detailSequence.current++
            setTarget(null)
            setDetail(null)
          }
        }}
      >
        <DialogContent className="max-h-[calc(100dvh-32px)] overflow-y-auto rounded-[12px] p-5 sm:max-w-sm">
          <DialogHeader className="flex-row items-center gap-3">
            <div className="grid size-11 shrink-0 place-items-center rounded-[8px] bg-primary/10 text-primary">
              <Bell className="size-5" />
            </div>
            <div className="min-w-0 space-y-2">
              <Badge variant="outline" className="w-fit">
                {messageTypeLabel(detail?.type || target?.type || "")}
              </Badge>
              <DialogTitle className="text-left">
                {detail?.title || target?.title || "消息详情"}
              </DialogTitle>
              <DialogDescription className="sr-only">
                查看通知内容及关联记录
              </DialogDescription>
            </div>
          </DialogHeader>
          {detailLoading ? (
            <div
              role="status"
              aria-label="正在读取通知详情"
              className="space-y-3"
            >
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-2/3" />
            </div>
          ) : detailError ? (
            <Notice error>
              {detailError}
              <Button
                variant="link"
                onClick={() => target && void show(target)}
              >
                重试
              </Button>
            </Notice>
          ) : (
            <p className="text-sm leading-6 whitespace-pre-wrap text-muted-foreground">
              {detail?.detail || detail?.preview}
            </p>
          )}
          <p className="text-xs text-muted-foreground">
            {date(detail?.created_at || target?.created_at || "")}
          </p>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline" className="h-10 rounded-[8px]">
                关闭
              </Button>
            </DialogClose>
            <Button
              className="h-10 rounded-[8px]"
              disabled={!detail || !providerMessageDestination(detail)}
              onClick={() => detail && related(detail)}
            >
              查看相关内容
              <ChevronRight className="size-4" />
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

type Contact = {
  name: string
  wechat_id: string
  wecom_service_url: string
  qrcode_url: string
  tips: string
  customer_reference: string
}
export function SupportPage() {
  const data = useLoad(() => api<Contact | null>("/support/contact-card/")),
    [notice, setNotice] = useState("")
  async function copy(value: string) {
    try {
      await navigator.clipboard.writeText(value)
      setNotice("已复制")
    } catch {
      setNotice("复制失败，请手动复制页面信息。")
    }
  }
  return (
    <div className="space-y-6">
      <PageHeader title="客服与帮助" back="/wallet" />
      <Notice tone={["已全部标记为已读", "已复制"].includes(notice) ? "success" : "info"}>{notice}</Notice>
      <SnapshotGate
        loading={data.loading}
        error={data.error}
        hasData={data.data != null}
        reload={() => data.reload().catch(() => {})}
      >
        <Panel>
          <h2 className="flex items-center gap-2 text-sm font-semibold">
            <Headphones className="size-4 text-primary" />
            钱包与押金咨询
          </h2>
          <p className="text-sm text-muted-foreground">
            带上账号信息，方便客服核对处理。
          </p>
          <Button asChild className="justify-self-start">
            <Link to="/support-chat">站内咨询客服</Link>
          </Button>
        </Panel>
        <LoadState
          hasData={data.data != null}
          loading={data.loading}
          error={data.error}
          reload={() => data.reload().catch(() => {})}
          empty={!data.data}
          emptyTitle="暂无客服资料"
          emptyDescription="可先通过站内会话咨询客服。"
        >
          {data.data && (
            <div className="grid gap-4 sm:grid-cols-2">
              <Panel>
                <h2 className="font-semibold">核对账号信息</h2>
                <p className="text-sm">
                  打手编号：{data.data.customer_reference}
                </p>
                <Button
                  variant="outline"
                  onClick={() =>
                    void copy(
                      `打手编号：${data.data!.customer_reference}，咨询钱包/押金事项`
                    )
                  }
                >
                  复制账号信息
                </Button>
              </Panel>
              <Panel>
                <h2 className="font-semibold">接待客服 · {data.data.name}</h2>
                {data.data.wechat_id && (
                  <div className="space-y-2">
                    <p className="text-sm">微信：{data.data.wechat_id}</p>
                    <Button
                      variant="outline"
                      onClick={() => void copy(data.data!.wechat_id)}
                    >
                      复制微信
                    </Button>
                  </div>
                )}
                {data.data.wecom_service_url &&
                  /^https:\/\//.test(data.data.wecom_service_url) && (
                    <Button variant="outline" asChild>
                      <a
                        href={data.data.wecom_service_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        企业微信客服
                      </a>
                    </Button>
                  )}
                {data.data.qrcode_url && (
                  <img
                    className="mx-auto size-40 object-contain"
                    width={160}
                    height={160}
                    src={data.data.qrcode_url}
                    alt="客服二维码"
                  />
                )}
                <p className="text-sm whitespace-pre-wrap text-muted-foreground">
                  {data.data.tips}
                </p>
              </Panel>
            </div>
          )}
        </LoadState>
        <Panel>
          <h2 className="font-semibold">钱包与押金须知</h2>
          <p className="text-sm leading-6 text-muted-foreground">
            陪玩钱包不支持普通充值；余额需要更正时，请联系客服核实并调账。现金押金线下补差由客服核验后直接登记。
          </p>
        </Panel>
      </SnapshotGate>
    </div>
  )
}

type ChatMessage = {
  id: number
  content: string
  content_type: "TEXT" | "IMAGE"
  image_url: string
  is_from_support: boolean
  is_read: boolean
  created_at: string
  client_message_id: string | null
  order_card?: { available: boolean; can_open?: boolean; id?: number; order_no?: string; service_name?: string; status_display?: string } | null
}
type History = {
  messages: ChatMessage[]
  has_more: boolean
  next_before_id: number | null
}
type PendingMessage = {
  id: string
  content: string
  draft: string
  scope: string
  kind: "TEXT" | "IMAGE"
  order_ref?: string
}
const mergeChat = (left: ChatMessage[], right: ChatMessage[]) =>
  [...new Map([...left, ...right].map((row) => [row.id, row])).values()].sort(
    (a, b) => a.created_at.localeCompare(b.created_at) || a.id - b.id
  )
const supportFaqs = [
  {
    question: "开始服务需要哪些凭证？",
    answer:
      "开始服务前上传入队截图，完成时上传结单截图；战绩凭证在报单中补齐。",
  },
  {
    question: "组队报名后为什么还不能开始？",
    answer:
      "需老板选择的组队订单，要先完成队友确认并由老板选定，才进入可服务阶段。",
  },
  {
    question: "提现结果如何核对？",
    answer: "先核对钱包中的提现记录与资金流水，结果不明时向客服提供申请信息。",
  },
]

export function SupportChatPage() {
  const [answer, setAnswer] = useState("")
  const [params, setParams] = useSearchParams(),
    scope = providerChatScope(params)
  const inputScope = useRef(scope)
  const [input, setInput] = useState(() => readChatDraft("provider", scope)),
    [pending, setPending] = useState<PendingMessage | null>(() =>
      readChatPending<PendingMessage>("provider")
    ),
    [imageFile, setImageFile] = useState<File | null>(null),
    [items, setItems] = useState<ChatMessage[]>([]),
    [before, setBefore] = useState<number | null>(null),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState(""),
    [abandon, setAbandon] = useState(false),
    [switchDraft, setSwitchDraft] = useState(false),
    history = useRef<HTMLDivElement>(null)
  const data = useLoad(
    () => api<History>(`/chat/session/${before ? `?before_id=${before}` : ""}`),
    [before]
  )
  useEffect(() => {
    if (!data.data) return
    setItems((old) =>
      [
        ...new Map(
          [...old, ...data.data!.messages].map((row) => [row.id, row])
        ).values(),
      ].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id - b.id)
    )
    if (!before)
      history.current?.scrollTo({ top: history.current.scrollHeight })
    const unread = data.data.messages
      .filter((row) => row.is_from_support && !row.is_read)
      .map((row) => row.id)
    if (
      mayMarkMessagesRead({
        hidden: document.hidden,
        focused: document.hasFocus(),
        browsing: !!before,
      })
    )
      for (let offset = 0; offset < unread.length; offset += 200)
        void api("/chat/read/", {
          method: "POST",
          data: { message_ids: unread.slice(offset, offset + 200) },
        }).catch(() => {})
  }, [data.data])
  useRefresh(async () => {
    const known = new Set(items.map((row) => row.id))
    let recent = await api<History>("/chat/session/")
    const fetched = [...recent.messages]
    while (
      known.size &&
      !fetched.some((row) => known.has(row.id)) &&
      recent.has_more &&
      recent.next_before_id
    ) {
      recent = await api<History>(
        `/chat/session/?before_id=${recent.next_before_id}`
      )
      fetched.push(...recent.messages)
    }
    setItems((old) => mergeChat(old, fetched))
    if (
      mayMarkMessagesRead({
        hidden: document.hidden,
        focused: document.hasFocus(),
        browsing: !!before,
      })
    ) {
      const unread = fetched
        .filter((row) => row.is_from_support && !row.is_read)
        .map((row) => row.id)
      for (let offset = 0; offset < unread.length; offset += 200)
        await api("/chat/read/", {
          method: "POST",
          data: { message_ids: unread.slice(offset, offset + 200) },
        })
      requestAnimationFrame(() =>
        history.current?.scrollTo({ top: history.current.scrollHeight })
      )
    }
  }, ["chat_message"])
  useEffect(() => {
    if (inputScope.current !== scope) {
      inputScope.current = scope
      setInput(readChatDraft("provider", scope))
      return
    }
    const saved = saveChatDraft("provider", scope, input)
    if (!saved.saved) setNotice(saved.error!.message)
  }, [input, scope])
  async function finish(result: ChatMessage, item: PendingMessage) {
    if (result.client_message_id !== item.id) return
    try {
      clearChatPending("provider", item.id)
      clearChatDraft("provider", item.scope, item.draft)
    } catch {
      /* 已由服务器核对 */
    }
    setPending(null)
    setImageFile(null)
    if (scope === item.scope && input.trim() === item.draft) setInput("")
    setItems((old) => [
      ...new Map([...old, result].map((row) => [row.id, row])).values(),
    ])
    setBefore(null)
    await data.reload().catch(() => {})
  }
  async function send(existing?: PendingMessage, selectedFile?: File) {
    if (busy || (pending && !existing)) return
    const file = selectedFile || imageFile,
      draft = input.trim(),
      content = scope ? `订单号 ${scope}${draft ? `\n${draft}` : ""}` : draft
    const item: PendingMessage = existing || {
      id: requestId("chat"),
      content,
      draft,
      scope,
      kind: selectedFile ? "IMAGE" : "TEXT",
      ...(scope ? { order_ref: scope } : {}),
    }
    if ((!draft && !selectedFile && !existing) || item.content.length > 1000) {
      setNotice("请填写消息，内容最多1000字。")
      return
    }
    if (file && file.size > 5 * 1024 * 1024) {
      setNotice("图片不能超过5MB。")
      return
    }
    setBusy(true)
    setNotice("")
    setPending(item)
    if (selectedFile) setImageFile(selectedFile)
    const storage = saveChatPending("provider", item)
    if (!storage.saved) setNotice(storage.error!.message)
    try {
      const result = await deliverChatMessage<ChatMessage>(
        "provider",
        item,
        file
      )
      await finish(result, item)
    } catch (cause) {
      if ([400, 404].includes((cause as { code?: number }).code || 0) && !existing) {
        setPending(null)
        clearChatPending("provider")
        setNotice(message(cause))
      } else setNotice("发送结果未确认，请先核对原消息，暂勿发送新消息。")
    } finally {
      setBusy(false)
    }
  }
  async function check() {
    if (!pending || busy) return
    setBusy(true)
    try {
      const result = await lookupChatMessage<ChatMessage>("provider", pending)
      if (result.status === "FOUND" && result.message) {
        await finish(result.message, pending)
        setNotice("原消息已发送，已核对。")
      } else
        setNotice(
          pending.kind === "IMAGE" && !imageFile
            ? "暂未查到原消息，请稍后继续核对。"
            : "暂未查到，可重试原消息。"
        )
    } catch {
      setNotice("暂时无法核对，请稍后重试。")
    } finally {
      setBusy(false)
    }
  }
  function removeContext() {
    const result = removeProviderChatContext(scope, input)
    if (result.conflict) {
      setSwitchDraft(true)
      return
    }
    if (!result.saved) {
      setNotice(result.error!.message)
      return
    }
    setParams({})
  }
  return (
    <div className="space-y-6">
      <PageHeader title="客服与帮助" />
      <Notice error={!!pending}>{notice}</Notice>
      {pending && (
        <div className="space-y-2 rounded-lg border border-primary/30 p-3">
          <p className="text-sm">
            待核对消息：
            {pending.kind === "IMAGE" ? "图片消息" : pending.content}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => void check()}
            >
              核对发送结果
            </Button>
            <Button
              variant="outline"
              disabled={busy || (pending.kind === "IMAGE" && !imageFile)}
              onClick={() => void send(pending)}
            >
              重试原消息
            </Button>
            <Button
              variant="ghost"
              disabled={busy}
              onClick={() => setAbandon(true)}
            >
              不再追踪
            </Button>
          </div>
        </div>
      )}
      <SnapshotGate
        loading={data.loading}
        error={data.error}
        hasData={data.data != null}
        reload={() => data.reload().catch(() => {})}
      >
        <div className="grid items-start gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
          <aside className="rounded-[12px] border bg-card p-4">
            <h2 className="flex items-center gap-2 text-sm font-semibold">
              <HelpCircle className="size-4 text-primary" />
              常见问题
            </h2>
            <div className="mt-3 space-y-2">
              {supportFaqs.map((item) => (
                <button
                  key={item.question}
                  type="button"
                  aria-pressed={answer === item.answer}
                  className={`min-h-11 w-full rounded-[8px] border px-3 py-2 text-left text-xs leading-5 hover:border-primary/30 ${answer === item.answer ? "border-primary bg-primary/5" : ""}`}
                  onClick={() => setAnswer(item.answer)}
                >
                  {item.question}
                </button>
              ))}
            </div>
            {answer && (
              <p
                role="status"
                className="mt-3 rounded-[8px] bg-muted/40 p-3 text-xs leading-5 text-muted-foreground"
              >
                {answer}
              </p>
            )}
            <div className="mt-4 border-t pt-4">
              <p className="flex items-center gap-2 text-xs font-medium">
                <ShieldCheck className="size-4 text-success" />
                资金问题请勿重复提交
              </p>
              <p className="mt-2 text-xs leading-5 text-muted-foreground">
                结果未确认时，先核对钱包记录，再联系客服。
              </p>
              <Button variant="outline" className="mt-4 h-11 w-full" asChild>
                <Link to="/support">查看客服与账户信息</Link>
              </Button>
            </div>
          </aside>
          <section className="overflow-hidden rounded-[12px] border bg-card">
            <div className="border-b p-4">
              <h2 className="text-sm font-semibold">平台客服会话</h2>
            </div>
            {scope && (
              <div className="flex items-center justify-between gap-3 rounded-lg bg-muted p-3 text-sm">
                <span className="break-all">关联订单：{scope}</span>
                <Button
                  variant="outline"
                  disabled={busy || !!pending}
                  onClick={removeContext}
                >
                  移除
                </Button>
              </div>
            )}
            <LoadState
              hasData={data.data != null}
              loading={data.loading}
              error={data.error}
              reload={() => data.reload().catch(() => {})}
            >
              <div
                ref={history}
                className="flex max-h-[420px] min-h-72 flex-col gap-4 overflow-y-auto p-4"
                aria-live="polite"
              >
                {data.data?.next_before_id && (
                  <Button
                    className="mx-auto"
                    variant="outline"
                    disabled={data.loading}
                    onClick={() => setBefore(data.data!.next_before_id)}
                  >
                    加载更早消息
                  </Button>
                )}
                {!items.length && (
                  <div className="my-auto">
                    <EmptyState
                      title="暂无客服会话"
                      description="发起新会话后，沟通记录会在这里显示。"
                    />
                  </div>
                )}
                {items.map((row) => (
                  <div
                    key={row.id}
                    className={`max-w-[85%] rounded-[12px] px-3 py-2.5 text-sm leading-6 ${row.is_from_support ? "self-start bg-muted/60" : "self-end bg-primary text-primary-foreground"}`}
                  >
                    {row.content_type === "IMAGE" && row.image_url && (
                      <PrivateImage src={row.image_url} alt="会话图片" />
                    )}
                    <p className="break-words whitespace-pre-wrap">
                      {row.content}
                    </p>
                    {row.order_card && (row.order_card.available ? row.order_card.can_open ? <Link className="mt-2 block rounded border p-2 underline" to={`/orders?order=${row.order_card.id}`}>订单 {row.order_card.order_no} · {row.order_card.service_name}<br />发送时：{row.order_card.status_display} · 查看订单</Link> : <p className="mt-2 rounded border p-2">订单 {row.order_card.order_no} · {row.order_card.service_name}<br />发送时：{row.order_card.status_display} · 可在接单池查看</p> : <p>关联订单当前不可查看</p>)}
                    <p
                      className={`mt-1 text-xs ${row.is_from_support ? "text-muted-foreground" : "text-primary-foreground/90 dark:text-primary-foreground/80"}`}
                    >
                      {row.is_from_support ? "平台客服" : "我"} ·{" "}
                      {date(row.created_at)}
                      {!row.is_from_support && (
                        <span className="ml-2">
                          {row.is_read ? "已读" : "已送达"}
                        </span>
                      )}
                    </p>
                  </div>
                ))}
              </div>
            </LoadState>
            <form
              onSubmit={(e) => {
                e.preventDefault()
                void send()
              }}
              className="border-t p-3"
            >
              <div className="flex items-end gap-2">
                <Textarea
                  aria-label="咨询内容"
                  className="h-10 min-w-0 flex-1 resize-none rounded-[8px] py-2 leading-5"
                  rows={1}
                  placeholder="描述问题，可附上订单号…"
                  value={input}
                  maxLength={1000}
                  onChange={(e) => setInput(e.target.value)}
                  disabled={busy}
                />
                <div className="flex shrink-0 gap-1.5">
                  <UploadField
                    aria-label="发送图片"
                    className="size-10 min-h-10 rounded-[8px] border-solid p-0 [&>svg]:size-4 [&>span]:sr-only"
                    accept="image/*"
                    disabled={busy || !!pending}
                    onChange={(e) => {
                      const file = e.target.files?.[0]
                      e.target.value = ""
                      if (file) void send(undefined, file)
                    }}
                  />
                  <Button
                    type="submit"
                    size="icon-lg"
                    className="shrink-0 rounded-[8px]"
                    aria-label={busy ? "发送中…" : "发送消息"}
                    disabled={busy || !!pending || !input.trim()}
                  >
                    <Send className="size-4" />
                  </Button>
                </div>
              </div>
            </form>
          </section>
        </div>
      </SnapshotGate>
      <Confirm
        open={abandon}
        title="停止追踪原消息"
        confirmVariant="destructive"
        description="停止追踪不会撤回已发送消息。请先核对会话记录，避免再次发送相同内容。"
        onClose={() => setAbandon(false)}
        onConfirm={() => {
          clearChatPending("provider")
          setPending(null)
          setImageFile(null)
          setAbandon(false)
        }}
        label="停止追踪"
      />
      <Confirm
        open={switchDraft}
        title="切换到普通会话草稿"
        description="普通会话已有其他草稿。当前订单草稿仍按订单单独保留，切换后显示普通草稿。"
        onClose={() => setSwitchDraft(false)}
        onConfirm={() => {
          setInput(readChatDraft("provider", ""))
          setParams({})
          setSwitchDraft(false)
        }}
      />
    </div>
  )
}
