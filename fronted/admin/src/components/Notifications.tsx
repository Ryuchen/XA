import { useEffect, useMemo, useRef, useState } from "react"
import { useNavigate } from "react-router"
import { useRealtime } from "@xa/shared"
import { ClipboardList, ChevronRight, Volume2, VolumeX } from "@/components/ui/icons"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { request } from "@/api/request"
import { useAdminAuth } from "@/lib/auth"
import { createStaffDispatchNotice } from "@/lib/staffDispatchNotice"
import { isUnreadCustomerMessage } from "@/lib/chatNotice"

class NoticeSound {
  enabled: boolean
  private context: AudioContext | null = null
  private master: GainNode | null = null
  private playing = false
  private key: string
  constructor(key: string) {
    this.key = key
    try {
      this.enabled = localStorage.getItem(key) !== "off"
    } catch {
      this.enabled = true
    }
  }
  unlock = async (preview = false) => {
    if (!this.enabled && !preview) return null
    try {
      if (!this.context || this.context.state === "closed") {
        this.context = new AudioContext()
        this.master = this.context.createGain()
        this.master.gain.value = 1
        this.master.connect(this.context.destination)
      }
      const context = this.context
      if (this.context.state !== "running") await this.context.resume()
      return context === this.context ? context : null
    } catch {
      return null
    }
  }
  toggle() {
    this.enabled = !this.enabled
    try {
      localStorage.setItem(this.key, this.enabled ? "on" : "off")
    } catch {}
    void this.unlock(true).then((context) => {
      if (context && context === this.context) this.play(true)
    })
  }
  play(preview = false) {
    if (
      (!this.enabled && !preview) ||
      !this.context ||
      !this.master ||
      this.context.state !== "running" ||
      (this.playing && !preview)
    )
      return
    try {
      const context = this.context,
        tone = context.createOscillator(),
        gain = context.createGain(),
        start = context.currentTime
      this.playing = true
      tone.type = "sine"
      tone.frequency.value = 880
      gain.gain.setValueAtTime(0, start)
      gain.gain.linearRampToValueAtTime(1, start + 0.008)
      gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.35)
      tone.connect(gain)
      gain.connect(this.master)
      tone.onended = () => {
        tone.disconnect()
        gain.disconnect()
        this.playing = false
      }
      tone.start(start)
      tone.stop(start + 0.35)
    } catch {
      this.playing = false
    }
  }
  dispose() {
    if (this.context) void this.context.close().catch(() => {})
    this.context = null
    this.master?.disconnect()
    this.master = null
    this.playing = false
  }
}
export default function Notifications() {
  const auth = useAdminAuth()
  const latest = useRef(auth)
  latest.current = auth
  const navigate = useNavigate()
  const sounds = useMemo(
    () => ({
      dispatch: new NoticeSound("xa_admin_dispatch_sound"),
      chat: new NoticeSound("xa_admin_chat_sound"),
    }),
    []
  )
  const [enabled, setEnabled] = useState({
    dispatch: sounds.dispatch.enabled,
    chat: sounds.chat.enabled,
  })
  const [queue, setQueue] = useState<{ status: string; count: number | null }>({
    status: "idle",
    count: null,
  })
  const chats = useRef<{
    toast: string | number
    sessions: Set<number>
  } | null>(null)
  const seenChats = useRef(new Set<number>())
  const seenPool = useRef(new Set<string>())
  const dispatch = useMemo(
    () =>
      createStaffDispatchNotice({
        session: () => latest.current.revision,
        allowed: () =>
          !!latest.current.token &&
          latest.current.hasPerm("order:view") &&
          latest.current.hasPerm("order:dispatch"),
        blocked: () =>
          Array.from(
            document.querySelectorAll<HTMLElement>(
              '[role="dialog"], [role="alertdialog"]'
            )
          ).some(
            (element) =>
              element.getClientRects().length > 0 &&
              getComputedStyle(element).visibility !== "hidden"
          ),
        retrieve: async (id) => (await request({ url: `/orders/${id}/` })).data,
        listPending: async () =>
          (
            await request({
              url: "/orders/",
              params: { actionable_staff: "1", page: 1, page_size: 1 },
            })
          ).data,
        queue: (status, count) => setQueue({ status, count }),
        show: (open, closed) => {
          sounds.dispatch.play()
          const id = toast.info("客服待派订单", {
            description: "有订单需要客服指派，请查看待派列表。",
            duration: 8000,
            action: { label: "查看待派", onClick: open },
            onDismiss: closed,
            onAutoClose: closed,
          })
          return { close: () => toast.dismiss(id) }
        },
        open: () => navigate("/orders?actionable_staff=1"),
      }),
    [auth.revision, navigate, sounds]
  )
  useEffect(() => {
    const unlock = () => {
      sounds.dispatch.unlock()
      sounds.chat.unlock()
    }
    for (const event of ["click", "touchend", "keydown"])
      document.addEventListener(event, unlock, true)
    let blocked = false
    const observer = new MutationObserver(() => {
      const current = !!document.querySelector(
        '[role="dialog"][data-state="open"], [role="alertdialog"][data-state="open"]'
      )
      if (current !== blocked) {
        blocked = current
        dispatch.modalChanged()
      }
    })
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["data-state", "aria-hidden"],
    })
    void dispatch.reconcile()
    return () => {
      for (const event of ["click", "touchend", "keydown"])
        document.removeEventListener(event, unlock, true)
      observer.disconnect()
      dispatch.reset()
      if (chats.current) toast.dismiss(chats.current.toast)
      chats.current = null
      seenChats.current.clear()
      seenPool.current.clear()
      sounds.dispatch.dispose()
      sounds.chat.dispose()
    }
  }, [dispatch, sounds])
  useRealtime(
    "admin",
    (event) => {
      if (event.type === "order_status_update")
        void dispatch.receive(event.data)
      if (event.type === "reconnected") void dispatch.reconcile()
      if (!auth.hasPerm("chat:view")) return
      if (event.type !== "chat_message" && event.type !== "chat_session_update") return
      const data = event.data,
        id = Number(data?.session_id),
        message = data?.message
      if (!Number.isSafeInteger(id) || id < 1 ||
        (!document.hidden && document.querySelector(`[data-chat-session="${id}"]`)))
        return
      if (event.type === "chat_message") {
        if (!isUnreadCustomerMessage(message) || seenChats.current.has(message.id)) return
        seenChats.current.add(message.id)
        if (seenChats.current.size > 300) seenChats.current.delete(seenChats.current.values().next().value!)
      } else {
        const session = data?.session
        const key = `${id}:${session?.last_message_at || ""}`
        if (!session || session.can_read || session.claimed_by || !session.unread_support || !session.last_message_at || seenPool.current.has(key)) return
        seenPool.current.add(key)
        if (seenPool.current.size > 300) seenPool.current.delete(seenPool.current.values().next().value!)
      }
      if (chats.current) {
        chats.current.sessions.add(id)
        return
      }
      const revision = auth.revision,
        sessions = new Set([id])
      sounds.chat.play()
      const close = () => {
        chats.current = null
      }
      const toastId = toast.info("新的客服咨询", {
        description: "有未读咨询，请查看。",
        duration: 8000,
        action: {
          label: "查看咨询",
          onClick: () => {
            toast.dismiss(toastId)
            chats.current = null
            if (latest.current.revision === revision)
              navigate(
                sessions.size === 1
                  ? `/chat?session_id=${id}`
                  : "/chat?only_unread=1"
              )
          },
        },
        onDismiss: close,
        onAutoClose: close,
      })
      chats.current = { toast: toastId, sessions }
    },
    !!auth.token
  )
  return (
    <div className="flex items-center gap-1">
      {auth.hasPerm("order:dispatch") && auth.hasPerm("order:view") && (
        <>
          <Button
            size="sm"
            variant="outline"
            className="admin-dispatch-entry"
            data-queue-state={queue.status === "error" || queue.status === "review" ? "review" : queue.count != null && queue.count > 0 ? "pending" : "idle"}
            aria-label={`订单待派，${queue.count != null ? `${queue.count} 笔` : queue.status === "error" ? "待读取" : "读取中"}${queue.status === "review" ? "，待核对" : ""}`}
            onClick={() => navigate("/orders?actionable_staff=1")}
          >
            <ClipboardList className="size-4" aria-hidden="true" />
            <span>订单待派</span>
            <span className="admin-dispatch-count">{queue.count ?? (queue.status === "error" ? "待读取" : "—")}</span>
            {queue.status === "review" && <span className="text-xs">待核对</span>}
            <ChevronRight className="size-3.5" aria-hidden="true" />
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            title="订单提示音"
            aria-pressed={enabled.dispatch}
            aria-label={enabled.dispatch ? "关闭订单提示音" : "开启订单提示音"}
            onClick={() => {
              sounds.dispatch.toggle()
              setEnabled((old) => ({
                ...old,
                dispatch: sounds.dispatch.enabled,
              }))
            }}
          >
            <span>订单音</span>
            {enabled.dispatch ? <Volume2 className="size-4" /> : <VolumeX className="size-4" />}
          </Button>
        </>
      )}
      {auth.hasPerm("chat:view") && (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          title="咨询提示音"
          className={enabled.chat ? "" : "text-muted-foreground"}
          aria-pressed={enabled.chat}
          aria-label={enabled.chat ? "关闭咨询提示音" : "开启咨询提示音"}
          onClick={() => {
            sounds.chat.toggle()
            setEnabled((old) => ({ ...old, chat: sounds.chat.enabled }))
          }}
        >
          <span>咨询音</span>
          {enabled.chat ? <Volume2 className="size-4" /> : <VolumeX className="size-4" />}
        </Button>
      )}
    </div>
  )
}
