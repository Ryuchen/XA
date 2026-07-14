import Taro from '@tarojs/taro';
import { getStoredToken } from '@/utils/auth';
import { BASE_URL } from '@/utils/request';

type MessageHandler = (data: unknown) => void;

class WebSocketService {
  private socketTask: Taro.SocketTask | null = null;
  private handlers: Map<string, Set<MessageHandler>> = new Map();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private wsUrl = '';
  private isConnected = false;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private shouldReconnect = false;

  connect() {
    const token = getStoredToken();
    if (!token) return;

    this.shouldReconnect = true;
    const wsBase = BASE_URL.replace('http://', 'ws://').replace('https://', 'wss://');
    this.wsUrl = `${wsBase}/ws/orders/?token=${token}`;
    this.doConnect();
  }

  private doConnect() {
    if (this.socketTask) {
      this.socketTask.close({});
      this.socketTask = null;
    }

    // 小程序端 connectSocket 直接返回 SocketTask；H5 端返回 Promise<SocketTask>。
    const task = Taro.connectSocket({ url: this.wsUrl, fail: () => {} });
    Promise.resolve(task)
      .then((socketTask) => this.bindSocketEvents(socketTask))
      .catch(() => this.scheduleReconnect());
  }

  private bindSocketEvents(socketTask: Taro.SocketTask) {
    this.socketTask = socketTask;

    socketTask.onOpen(() => {
      this.isConnected = true;
      this.startHeartbeat();
    });

    socketTask.onMessage((res) => {
      try {
        const msg = JSON.parse(res.data as string);
        if (msg.type === 'pong') return;
        this.dispatch(msg.type, msg.data);
      } catch {
        /* ignore parse errors */
      }
    });

    socketTask.onClose(() => {
      this.isConnected = false;
      this.stopHeartbeat();
      this.scheduleReconnect();
    });

    socketTask.onError(() => {
      this.isConnected = false;
      this.stopHeartbeat();
      this.scheduleReconnect();
    });
  }

  private startHeartbeat() {
    this.heartbeatTimer = setInterval(() => {
      if (this.isConnected) {
        this.socketTask?.send({ data: '{"type":"ping"}' });
      }
    }, 30000);
  }

  private stopHeartbeat() {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  private scheduleReconnect() {
    if (!this.shouldReconnect) return;
    if (this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.doConnect();
    }, 3000);
  }

  on(type: string, handler: MessageHandler) {
    if (!this.handlers.has(type)) {
      this.handlers.set(type, new Set());
    }
    this.handlers.get(type)!.add(handler);
    return () => {
      this.handlers.get(type)?.delete(handler);
    };
  }

  private dispatch(type: string, data: unknown) {
    this.handlers.get(type)?.forEach((handler) => handler(data));
  }

  disconnect() {
    this.shouldReconnect = false;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.stopHeartbeat();
    this.socketTask?.close({});
    this.socketTask = null;
    this.isConnected = false;
  }
}

export const wsService = new WebSocketService();
