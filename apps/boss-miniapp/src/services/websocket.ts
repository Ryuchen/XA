import Taro from '@tarojs/taro';
import { getStoredToken } from '@/utils/auth';
import { request, ApiResponse } from '@/utils/request';
import { WS_URL } from '@/utils/env';

type MessageHandler = (data: unknown) => void;

class WebSocketService {
  private socketTask: Taro.SocketTask | null = null;
  private handlers = new Map<string, Set<MessageHandler>>();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private replyTimer: ReturnType<typeof setTimeout> | null = null;
  private openingTimer: ReturnType<typeof setTimeout> | null = null;
  private shouldReconnect = false;
  private connecting = false;
  private connectionToken = '';
  private generation = 0;
  private attempts = 0;

  constructor() {
    Taro.eventCenter.on('xa:auth.changed', () => {
      if (!getStoredToken()) this.disconnect();
      else if (this.shouldReconnect) this.connect();
    });
  }

  connect() {
    const token = getStoredToken();
    if (!token) { this.disconnect(); return; }
    this.shouldReconnect = true;
    if (token === this.connectionToken && (this.socketTask || this.connecting)) return;
    this.clearRetry();
    void this.doConnect();
  }

  private async doConnect(recover = false) {
    this.releaseConnection();
    if (!this.shouldReconnect || !getStoredToken()) return;
    const generation = this.generation;
    this.connecting = true;
    this.connectionToken = getStoredToken();
    try {
      // HTTP owns token refresh and definitive login expiry; a websocket retry
      // must not keep presenting the access token captured before an outage.
      if (recover) {
        const response = await request<ApiResponse>('/users/me/');
        if (response.code !== 0) throw new Error('Session unavailable');
      }
      if (generation !== this.generation || !this.shouldReconnect) return;
      const token = getStoredToken();
      if (!token) { this.disconnect(); return; }
      this.connectionToken = token;
      const wsBase = WS_URL;
      this.openingTimer = setTimeout(() => this.lost(generation), 10000);
      const task = await Promise.resolve(Taro.connectSocket({
        url: `${wsBase}/ws/orders/?token=${encodeURIComponent(token)}`, fail: () => {},
      }));
      if (generation !== this.generation || !this.shouldReconnect) { task.close({}); return; }
      this.socketTask = task;
      this.connecting = false;
      const current = () => generation === this.generation && this.socketTask === task;
      task.onOpen(() => {
        if (!current()) return;
        if (this.openingTimer) clearTimeout(this.openingTimer);
        this.openingTimer = null;
        this.attempts = 0;
        const ping = () => {
          if (!current()) return;
          if (getStoredToken() !== this.connectionToken) { this.connect(); return; }
          this.replyTimer = setTimeout(() => this.lost(generation), 10000);
          task.send({ data: '{"type":"ping"}', fail: () => this.lost(generation) });
        };
        ping();
        if (!current()) return;
        this.heartbeatTimer = setInterval(ping, 25000);
        this.dispatch('connected', undefined);
      });
      task.onMessage(result => {
        if (!current()) return;
        if (getStoredToken() !== this.connectionToken) { this.connect(); return; }
        try {
          const message = JSON.parse(result.data as string);
          if (message.type === 'pong') {
            if (this.replyTimer) clearTimeout(this.replyTimer);
            this.replyTimer = null;
          } else this.dispatch(message.type, message.data);
        } catch { /* HTTP remains the source of truth. */ }
      });
      task.onClose(() => this.lost(generation));
      task.onError(() => this.lost(generation));
    } catch {
      this.lost(generation);
    }
  }

  private lost(generation: number) {
    if (generation !== this.generation) return;
    this.releaseConnection();
    if (!this.shouldReconnect || this.reconnectTimer || !getStoredToken()) return;
    const delay = Math.min(30000, 3000 * 2 ** Math.min(this.attempts++, 4)) + Math.random() * 500;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      void this.doConnect(true);
    }, delay);
  }

  private releaseConnection() {
    ++this.generation;
    for (const timer of [this.replyTimer, this.openingTimer]) if (timer) clearTimeout(timer);
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.replyTimer = this.openingTimer = this.heartbeatTimer = null;
    const previous = this.socketTask;
    this.socketTask = null;
    this.connecting = false;
    this.connectionToken = '';
    // Detach before close: its delayed callbacks belong to the old generation.
    if (previous) { try { previous.close({}); } catch { /* already closed */ } }
  }

  private clearRetry() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
  }

  on(type: string, handler: MessageHandler) {
    if (!this.handlers.has(type)) this.handlers.set(type, new Set());
    this.handlers.get(type)!.add(handler);
    return () => { this.handlers.get(type)?.delete(handler); };
  }

  private dispatch(type: string, data: unknown) {
    this.handlers.get(type)?.forEach(handler => handler(data));
  }

  disconnect() {
    this.shouldReconnect = false;
    this.attempts = 0;
    this.clearRetry();
    this.releaseConnection();
  }
}

export const wsService = new WebSocketService();
