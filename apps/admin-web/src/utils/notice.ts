import { reactive } from 'vue'

type Notice = { id: number; message: string; type: 'success' | 'error' }
export const notices = reactive<Notice[]>([])
let nextId = 0
export function dismissNotice(id: number) { const index = notices.findIndex(item => item.id === id); if (index >= 0) notices.splice(index, 1) }
export function showNotice(message: string, type: Notice['type'] = 'error') {
  const id = ++nextId
  notices.push({ id, message, type })
  window.setTimeout(() => dismissNotice(id), 5000)
}

type InteractiveNotice = { id: number; title: string; message: string; onClick: () => void; close: () => void }
export const interactiveNotices = reactive<InteractiveNotice[]>([])
export function showInteractiveNotice(options: { title: string; message: string; duration: number; onClick: () => void; onClose: () => void }) {
  const id = ++nextId
  let closed = false
  const timer = window.setTimeout(() => close(), options.duration)
  function close() {
    if (closed) return
    closed = true
    window.clearTimeout(timer)
    const index = interactiveNotices.findIndex(item => item.id === id)
    if (index >= 0) interactiveNotices.splice(index, 1)
    options.onClose()
  }
  interactiveNotices.push({ id, title: options.title, message: options.message, onClick: options.onClick, close })
  return { close }
}
