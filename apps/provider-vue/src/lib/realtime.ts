import { accessToken, api } from './session'

type Handler = (data: unknown) => void
const listeners = new Map<string, Set<Handler>>()
let socket: WebSocket | null = null, timer: ReturnType<typeof setTimeout> | null = null
let heartbeat: ReturnType<typeof setInterval> | null = null, deadline: ReturnType<typeof setTimeout> | null = null
let active = false, generation = 0, attempts = 0, connectedToken = ''
function emit(type: string, data?: unknown) { listeners.get(type)?.forEach(handler => handler(data)) }
export function onRealtime(type: string, handler: Handler) {
  if (!listeners.has(type)) listeners.set(type, new Set())
  listeners.get(type)!.add(handler)
  return () => { listeners.get(type)?.delete(handler) }
}
function release() {
  generation++
  if (heartbeat) clearInterval(heartbeat)
  if (deadline) clearTimeout(deadline)
  heartbeat = null; deadline = null; connectedToken = ''
  const previous = socket; socket = null; previous?.close()
}
function retry(current: number) {
  if (current !== generation) return
  release()
  if (!active || !accessToken() || timer) return
  const delay = Math.min(30000, 3000 * 2 ** Math.min(attempts++, 4)) + Math.random() * 500
  timer = setTimeout(() => { timer = null; void open(true) }, delay)
}
async function open(recover = false) {
  release()
  const current = generation
  if (!active || !accessToken()) return
  try {
    if (recover) await api('/users/me/')
    if (current !== generation || !active) return
    const token = accessToken(); if (!token) return
    connectedToken = token
    const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/orders/?token=${encodeURIComponent(token)}`)
    socket = ws
    deadline = setTimeout(() => retry(current), 10000)
    ws.onopen = () => {
      if (current !== generation) return
      if (deadline) clearTimeout(deadline)
      deadline = null; attempts = 0
      const ping = () => {
        if (current !== generation) return
        if (connectedToken !== accessToken()) { void open(); return }
        if (ws.readyState !== WebSocket.OPEN) { retry(current); return }
        deadline = setTimeout(() => retry(current), 10000)
        ws.send('{"type":"ping"}')
      }
      ping(); heartbeat = setInterval(ping, 25000); emit('connected')
    }
    ws.onmessage = event => {
      if (current !== generation) return
      try {
        const message = JSON.parse(String(event.data)) as { type: string; data?: unknown }
        if (message.type === 'pong') { if (deadline) clearTimeout(deadline); deadline = null }
        else emit(message.type, message.data)
      } catch { /* HTTP reads remain authoritative. */ }
    }
    ws.onclose = () => retry(current)
    ws.onerror = () => retry(current)
  } catch { retry(current) }
}
export function startRealtime() { if (active) return; active = true; void open() }
export function stopRealtime() { active = false; attempts = 0; if (timer) clearTimeout(timer); timer = null; release() }

export function coalesced(refresh: () => Promise<unknown>) {
  let timer: ReturnType<typeof setTimeout> | null = null, running = false, pending = false, disposed = false
  const schedule = () => {
    if (disposed || running || timer || !pending) return
    timer = setTimeout(async () => {
      timer = null; if (disposed) return
      pending = false; running = true
      try { await refresh() } finally { running = false; schedule() }
    }, 300 + Math.random() * 200)
  }
  return { request() { pending = true; schedule() }, dispose() { disposed = true; if (timer) clearTimeout(timer) } }
}
