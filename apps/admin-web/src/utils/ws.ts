import { useAuthStore } from '@/stores/auth'

type MessageHandler = (data: any) => void

/** One connection per session, with HTTP reconciliation after reconnect. */
class AdminWebSocket {
  private socket: WebSocket | null = null
  private handlers = new Map<string, Set<MessageHandler>>()
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null
  private replyTimer: ReturnType<typeof setTimeout> | null = null
  private openingTimer: ReturnType<typeof setTimeout> | null = null
  private shouldReconnect = false
  private connecting = false
  private connectionToken = ''
  private generation = 0
  private attempts = 0

  connect() {
    const token = useAuthStore().token
    if (!token) { this.disconnect(); return }
    this.shouldReconnect = true
    if (token === this.connectionToken && (this.socket || this.connecting)) return
    this.clearRetry()
    void this.doConnect()
  }

  private async doConnect(recover = false) {
    this.releaseConnection()
    if (!this.shouldReconnect || !useAuthStore().token) return
    const generation = this.generation
    this.connecting = true
    this.connectionToken = useAuthStore().token
    try {
      // Uses the normal HTTP refresh/logout path if the access token expired.
      if (recover) await useAuthStore().fetchProfile()
      if (generation !== this.generation || !this.shouldReconnect) return
      const token = useAuthStore().token
      if (!token) { this.disconnect(); return }
      this.connectionToken = token
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      const socket = new WebSocket(`${proto}://${location.host}/ws/orders/?token=${encodeURIComponent(token)}`)
      this.socket = socket
      this.connecting = false
      this.openingTimer = setTimeout(() => this.lost(generation), 10000)
      const current = () => generation === this.generation && this.socket === socket
      socket.onopen = () => {
        if (!current()) return
        if (this.openingTimer) clearTimeout(this.openingTimer)
        this.openingTimer = null
        this.attempts = 0
        const ping = () => {
          if (!current()) return
          if (useAuthStore().token !== this.connectionToken) { this.connect(); return }
          this.replyTimer = setTimeout(() => this.lost(generation), 10000)
          try { socket.send('{"type":"ping"}') } catch { this.lost(generation) }
        }
        ping()
        if (!current()) return
        this.heartbeatTimer = setInterval(ping, 25000)
        this.dispatch('connected', undefined)
      }
      socket.onmessage = event => {
        if (!current()) return
        if (useAuthStore().token !== this.connectionToken) { this.connect(); return }
        try {
          const message = JSON.parse(event.data)
          if (message.type === 'pong') {
            if (this.replyTimer) clearTimeout(this.replyTimer)
            this.replyTimer = null
          } else this.dispatch(message.type, message.data)
        } catch { /* HTTP remains the source of truth. */ }
      }
      socket.onclose = () => this.lost(generation)
      socket.onerror = () => this.lost(generation)
    } catch { this.lost(generation) }
  }

  private lost(generation: number) {
    if (generation !== this.generation) return
    this.releaseConnection()
    if (!this.shouldReconnect || this.reconnectTimer || !useAuthStore().token) return
    const delay = Math.min(30000, 3000 * 2 ** Math.min(this.attempts++, 4)) + Math.random() * 500
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      void this.doConnect(true)
    }, delay)
  }

  private releaseConnection() {
    ++this.generation
    for (const timer of [this.replyTimer, this.openingTimer]) if (timer) clearTimeout(timer)
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer)
    this.replyTimer = this.openingTimer = this.heartbeatTimer = null
    const previous = this.socket
    this.socket = null
    this.connecting = false
    this.connectionToken = ''
    if (previous) { try { previous.close() } catch { /* already closed */ } }
  }

  private clearRetry() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.reconnectTimer = null
  }

  on(type: string, handler: MessageHandler) {
    if (!this.handlers.has(type)) this.handlers.set(type, new Set())
    this.handlers.get(type)!.add(handler)
    return () => { this.handlers.get(type)?.delete(handler) }
  }

  private dispatch(type: string, data: any) {
    this.handlers.get(type)?.forEach(handler => handler(data))
  }

  disconnect() {
    this.shouldReconnect = false
    this.attempts = 0
    this.clearRetry()
    this.releaseConnection()
  }
}

export const adminWs = new AdminWebSocket()
