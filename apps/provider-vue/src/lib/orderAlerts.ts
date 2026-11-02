import { accessToken, api, sessionRevision } from './session'
import { coalesced, onRealtime } from './realtime'

type PoolItem = { id: number; status: string; reject_count?: number; dispatch_notification_enabled?: boolean }
type Cursor = { latest_id: number; pending: boolean }
let running = false, generation = 0, initialized = false, assignmentCursor: number | null = null
let poll: ReturnType<typeof setInterval> | null = null, inFlight: Promise<PoolItem[]> | null = null
let poolQueue: ReturnType<typeof coalesced> | null = null, assignmentQueue: ReturnType<typeof coalesced> | null = null
let offs: (() => void)[] = [], audio: AudioContext | null = null
const seen = new Map<number, number>(), assignments = new Set<number>(), tones = new Set<OscillatorNode>()
const subscribers = new Set<(orders: PoolItem[]) => void>()

function storedSound() {
  try { const raw = localStorage.getItem('xa_provider_order_sound'); if (!raw) return true; try { return JSON.parse(raw).data !== 'off' } catch { return raw !== 'off' } } catch { return true }
}
export function soundEnabled() { return storedSound() }
export function setSoundEnabled(value: boolean) { try { localStorage.setItem('xa_provider_order_sound', JSON.stringify({ data: value ? 'on' : 'off' })) } catch { /* optional setting */ } if (!value) stopSound() }
function unlock() {
  try {
    const AudioClass = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    if (!AudioClass) return
    if (!audio || audio.state === 'closed') audio = new AudioClass()
    if (audio.state === 'suspended') void audio.resume().catch(() => {})
  } catch { /* Audio is optional. */ }
}
function stopSound() { for (const tone of tones) { try { tone.stop() } catch { /* already ended */ } } tones.clear() }
function play() {
  if (document.hidden || !soundEnabled() || !audio || audio.state !== 'running') return
  try {
    stopSound(); const context = audio, start = context.currentTime
    for (const [frequency, volume] of [[880, .28], [1320, .12], [1760, .06]]) {
      const tone = context.createOscillator(), gain = context.createGain(); tone.frequency.value = frequency
      gain.gain.setValueAtTime(0, start); gain.gain.linearRampToValueAtTime(volume, start + .008); gain.gain.exponentialRampToValueAtTime(.0001, start + .65)
      tone.connect(gain); gain.connect(context.destination); tones.add(tone)
      tone.onended = () => { tone.disconnect(); gain.disconnect(); tones.delete(tone) }; tone.start(start); tone.stop(start + .65)
    }
  } catch { /* Order operations do not depend on audio. */ }
}
export function onPoolUpdate<T extends PoolItem>(handler: (orders: T[]) => void) { const subscriber = handler as (orders: PoolItem[]) => void; subscribers.add(subscriber); return () => { subscribers.delete(subscriber) } }
export function loadPending<T extends PoolItem = PoolItem>(): Promise<T[]> {
  if (inFlight) return inFlight as Promise<T[]>
  const current = generation, revision = sessionRevision()
  const operation = api<PoolItem[]>('/orders/orders/?status=pending&role=provider').then(rows => {
    if (!running || current !== generation || revision !== sessionRevision()) return rows
    let hasNew = false
    for (const order of rows) {
      if (order.status !== 'PENDING') continue
      const rejection = order.reject_count ?? 0, previous = seen.get(order.id)
      if ((previous === undefined || rejection > previous) && order.dispatch_notification_enabled !== false) hasNew = true
      seen.set(order.id, rejection)
    }
    if (initialized && hasNew) play()
    initialized = true; subscribers.forEach(handler => handler(rows)); return rows
  })
  inFlight = operation
  void operation.finally(() => { if (inFlight === operation) inFlight = null }).catch(() => {})
  return operation as Promise<T[]>
}
export async function loadPendingAfterCurrent<T extends PoolItem = PoolItem>(): Promise<T[]> {
  const current = inFlight
  if (current) { try { await current } catch { /* A fresh read follows either outcome. */ } }
  return loadPending<T>()
}
async function refreshPool() { if (!running || document.hidden || !accessToken()) return; try { await loadPending() } catch { /* retry on the next update */ } }
async function refreshAssignments() {
  if (!running || document.hidden || !accessToken()) return
  const current = generation, revision = sessionRevision(), after = assignmentCursor
  try {
    const data = await api<Cursor>(`/messages/assignment-alerts/${after === null ? '' : `?after=${after}`}`)
    if (!running || current !== generation || revision !== sessionRevision() || !Number.isSafeInteger(data.latest_id) || data.latest_id < 0) return
    if (after !== null && data.pending && data.latest_id > (assignmentCursor ?? after)) play()
    assignmentCursor = Math.max(assignmentCursor ?? 0, data.latest_id)
  } catch { /* retry after reconnect */ }
}
function visible() { if (!document.hidden) { poolQueue?.request(); assignmentQueue?.request() } else stopSound() }
export function startOrderAlerts() {
  if (running) return
  running = true; generation++; initialized = false; assignmentCursor = null; seen.clear(); assignments.clear()
  for (const event of ['click', 'touchend', 'keydown']) document.addEventListener(event, unlock, true)
  document.addEventListener('visibilitychange', visible)
  poolQueue = coalesced(refreshPool); assignmentQueue = coalesced(refreshAssignments)
  offs = [onRealtime('order_status_update', () => poolQueue?.request()), onRealtime('connected', () => { poolQueue?.request(); assignmentQueue?.request() }), onRealtime('site_message_update', data => {
    const id = (data as { assignment_message_id?: unknown } | null)?.assignment_message_id
    if (!running || document.hidden || !Number.isSafeInteger(id) || (id as number) <= 0 || assignments.has(id as number) || (assignmentCursor !== null && (id as number) <= assignmentCursor)) return
    assignments.add(id as number); assignmentCursor = Math.max(assignmentCursor ?? 0, id as number); play()
  })]
  poll = setInterval(() => { void refreshPool() }, 10000)
  void refreshPool(); void refreshAssignments()
}
export function stopOrderAlerts() {
  running = false; generation++; inFlight = null
  if (poll) clearInterval(poll); poll = null
  poolQueue?.dispose(); assignmentQueue?.dispose(); poolQueue = assignmentQueue = null
  offs.forEach(off => off()); offs = []; stopSound()
  for (const event of ['click', 'touchend', 'keydown']) document.removeEventListener(event, unlock, true)
  document.removeEventListener('visibilitychange', visible)
}
