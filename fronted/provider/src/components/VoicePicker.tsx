import { useCallback, useEffect, useState } from "react"
import { api, message, requestId, useViewGuard } from "@/lib/provider"
import { Button } from "@/components/ui/button"
import { PrivateAudio, storeStorageKey, useSession } from "@xa/shared"

type Recording = { id: number; title: string; duration_ms: number; audio_url: string; available: boolean }
type Page = { list: Recording[]; page: number; has_more: boolean }
type Resolution = { status: "FOUND" | "NOT_FOUND" | "REMOVED"; recording?: Recording }
function readPending(prefix: string): string | null {
  try {
    const ids: string[] = []
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i)
      if (!key?.startsWith(prefix)) continue
      const id = key.slice(prefix.length)
      if (/^[a-z0-9_-]{8,64}$/.test(id)) ids.push(id)
    }
    return ids.sort()[0] || null
  } catch { return null }
}

export function VoicePicker({ value, onChange, onBusyChange }: {
  value: number | null; onChange: (id: number | null) => void; onBusyChange: (busy: boolean) => void
}) {
  const session = useSession("provider")
  const guardView = useViewGuard()
  const legacyKey = storeStorageKey(`xa:voice-upload:v1:${session.user?.id || ""}`)
  const pendingPrefix = storeStorageKey(`xa:voice-upload:v2:${session.user?.id || ""}:`)
  const [pending, setPending] = useState<string | null>(() => readPending(pendingPrefix))
  useEffect(() => {
    // Preserve an unfinished upload from the earlier per-tab implementation.
    try {
      const old = sessionStorage.getItem(legacyKey)
      if (old && /^[a-z0-9_-]{8,64}$/.test(old)) {
        localStorage.setItem(pendingPrefix + old, "1")
        sessionStorage.removeItem(legacyKey)
      }
    } catch { /* Uploading remains disabled if recovery storage is unavailable. */ }
    const refresh = () => setPending(readPending(pendingPrefix))
    refresh()
    const listener = (event: StorageEvent) => {
      if (!event.key || event.key.startsWith(pendingPrefix)) refresh()
    }
    window.addEventListener("storage", listener)
    return () => window.removeEventListener("storage", listener)
  }, [legacyKey, pendingPrefix])
  const [rows, setRows] = useState<Recording[]>([])
  const [page, setPage] = useState(1)
  const [more, setMore] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const load = useCallback(async (next: number) => {
    const current = guardView()
    setLoading(true)
    setError("")
    try {
      const result = await api<Page>(`/users/recordings/?page=${next}`)
      if (!current()) return
      setRows(old => next === 1 ? result.list : [...old, ...result.list])
      setPage(next)
      setMore(result.has_more)
    } catch (cause) { if (current()) setError(message(cause)) }
    finally { if (current()) setLoading(false) }
  }, [guardView])
  useEffect(() => { void load(1) }, [load])
  useEffect(() => {
    if (value && rows.some(row => row.id === value && !row.available)) onChange(null)
  }, [rows, value, onChange])
  useEffect(() => { onBusyChange(!!pending); return () => onBusyChange(false) }, [pending, onBusyChange])
  function clearPending(id: string) {
    try { localStorage.removeItem(pendingPrefix + id) } catch { /* already saved; the next lookup can recover */ }
    setPending(readPending(pendingPrefix))
  }
  async function accept(result: Recording, id: string) {
    clearPending(id)
    onChange(result.id)
    await load(1)
    setRows(old => old.some(row => row.id === result.id) ? old : [result, ...old])
  }
  async function resolve() {
    if (!pending) return
    const current = guardView()
    setLoading(true); setError("")
    try {
      const result = await api<Resolution>(`/users/recordings/resolve/?client_request_id=${encodeURIComponent(pending)}`)
      if (!current()) return
      if (result.status === "FOUND" && result.recording) await accept(result.recording, pending)
      else if (result.status === "REMOVED") { clearPending(pending); setError("该录音已移除，请重新选择。") }
      else setError("暂未查到上传结果；请求可能仍在处理中。可稍后核对，或选择同一文件重试。")
    } catch (cause) { if (current()) setError(`${message(cause)}，请稍后再次核对。`) }
    finally { if (current()) setLoading(false) }
  }
  async function upload(file: File) {
    const current = guardView()
    const id = pending || requestId("voice")
    try { localStorage.setItem(pendingPrefix + id, "1") } catch {
      setError("无法保存上传恢复信息，请检查浏览器存储后重试。")
      return
    }
    setPending(id)
    setLoading(true)
    onBusyChange(true)
    setError("")
    try {
      const form = new FormData()
      form.append("audio", file)
      form.append("client_request_id", id)
      const result = await api<Recording>("/users/recordings/", { method: "POST", data: form })
      if (!current()) return
      await accept(result, id)
    } catch (cause) {
      if (!current()) return
      // Keep the key even for validation failures: a previous attempt with
      // this key may still be in flight and commit after a retry is rejected.
      setError(`${message(cause)}，请核对上传结果；若原文件无效，可重新选择音频重试。`)
    }
    finally { if (current()) setLoading(false) }
  }
  const selected = rows.find(row => row.id === value)
  return <div className="space-y-2 rounded-md border p-3">
    <label className="block text-sm font-medium" htmlFor="audition-recording">选择本人试音录音</label>
    <select id="audition-recording" className="h-10 w-full rounded-md border bg-background px-2 text-sm" value={value ?? ""}
      disabled={loading} onChange={event => onChange(event.target.value ? Number(event.target.value) : null)}>
      <option value="">请选择录音</option>
      {rows.map(row => <option key={row.id} value={row.id} disabled={!row.available}>{row.title} · {Math.ceil(row.duration_ms / 1000)} 秒{!row.available ? " · 暂不可用" : ""}</option>)}
    </select>
    {selected?.available && <PrivateAudio role="provider" className="w-full" src={selected.audio_url} label="试听本人录音" />}
    {selected && !selected.available && <p role="alert" className="text-sm text-destructive">录音暂不可用，请选择其他录音或重新上传。</p>}
    {more && <Button type="button" variant="outline" disabled={loading} onClick={() => void load(page + 1)}>加载更多录音</Button>}
    {pending && <div className="space-y-2"><p className="text-sm">上次上传结果待核对。</p>
      <Button type="button" variant="outline" disabled={loading} onClick={() => void resolve()}>核对上传结果</Button></div>}
    <label className="block text-sm">上传新录音
      <input className="mt-1 block w-full text-sm" type="file" accept=".mp3,.m4a,.wav,.ogg,.opus,audio/*" disabled={loading}
        onChange={event => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void upload(file) }} />
    </label>
    <p className="text-xs text-muted-foreground">上传成功后保存到本人录音库，可再次使用。</p>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
  </div>
}
