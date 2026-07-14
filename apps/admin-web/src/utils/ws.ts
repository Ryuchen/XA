import { useAuthStore } from '@/stores/auth'

type MessageHandler = (data: any) => void

/**
 * 后台 WebSocket 客户端：复用后端 /ws/orders/ 单连接，
 * token 走 query string（与 C 端一致），自动重连 + 心跳。
 */
class AdminWebSocket {
  private socket: WebSocket | null = null
  private handlers = new Map<string, Set<MessageHandler>>()
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null
  private shouldReconnect = false

  connect() {
    const auth = useAuthStore()
    if (!auth.token) return
    this.shouldReconnect = true
    this.doConnect(auth.token)
  }

  private doConnect(token: string) {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const url = `${proto}://${location.host}/ws/orders/?token=${token}`
    this.socket = new WebSocket(url)

    this.socket.onopen = () => {
      this.startHeartbeat()
    }
    this.socket.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data)
        if (msg.type === 'pong') return
        this.dispatch(msg.type, msg.data)
      } catch {
        /* ignore */
      }
    }
    this.socket.onclose = () => {
      this.stopHeartbeat()
      this.scheduleReconnect()
    }
    this.socket.onerror = () => {
      this.stopHeartbeat()
      this.socket?.close()
    }
  }

  private startHeartbeat() {
    this.heartbeatTimer = setInterval(() => {
      if (this.socket?.readyState === WebSocket.OPEN) {
        this.socket.send('{"type":"ping"}')
      }
    }, 30000)
  }

  private stopHeartbeat() {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer)
      this.heartbeatTimer = null
    }
  }

  private scheduleReconnect() {
    if (!this.shouldReconnect || this.reconnectTimer) return
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      const auth = useAuthStore()
      if (auth.token) this.doConnect(auth.token)
    }, 3000)
  }

  on(type: string, handler: MessageHandler) {
    if (!this.handlers.has(type)) this.handlers.set(type, new Set())
    this.handlers.get(type)!.add(handler)
    return () => {
      this.handlers.get(type)?.delete(handler)
    }
  }

  private dispatch(type: string, data: any) {
    this.handlers.get(type)?.forEach((h) => h(data))
  }

  disconnect() {
    this.shouldReconnect = false
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.stopHeartbeat()
    this.socket?.close()
    this.socket = null
  }
}

export const adminWs = new AdminWebSocket()
