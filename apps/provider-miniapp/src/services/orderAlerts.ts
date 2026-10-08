import Taro from '@tarojs/taro';
import { ProviderOrder } from '@/types/order';
import { getSessionRevision, getStoredToken } from '@/utils/auth';
import { createCoalescedRefresh } from '@/utils/coalescedRefresh';
import { cooldownRemaining } from '@/utils/orderNetwork';
import { orderAlertSound } from '@/utils/orderAlertSound';
import { ApiResponse, request } from '@/utils/request';
import { wsService } from './websocket';

type PoolResponse = ApiResponse<ProviderOrder[]>;

/** Share the visible pool between the workbench and app-wide new-order alerts. */
class OrderAlerts {
  private foreground = false;
  private installed = false;
  private generation = 0;
  private revision = -1;
  private initialized = false;
  private seen = new Set<number>();
  private inFlight: Promise<PoolResponse> | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private queue: ReturnType<typeof createCoalescedRefresh> | null = null;
  private offSocket: Array<() => void> = [];
  private subscribers = new Set<(response: PoolResponse) => void>();

  private syncSession() {
    const revision = getSessionRevision();
    if (this.revision === revision) return;
    this.revision = revision;
    this.initialized = false;
    this.seen.clear();
    this.inFlight = null;
    orderAlertSound.stop();
  }

  loadPending(): Promise<PoolResponse> {
    this.syncSession();
    if (this.inFlight) return this.inFlight;
    const revision = this.revision, generation = this.generation;
    const operation = request<PoolResponse>('/orders/orders/?status=pending&role=provider', 'GET').then(response => {
      if (revision !== getSessionRevision() || generation !== this.generation || !this.foreground || !getStoredToken()) return response;
      if (response.code === 0 && Array.isArray(response.data)) {
        // The server applies service skills and pass delays; broadcasts alone
        // cannot tell us whether a provider can see an order yet.
        let hasNewOrder = false;
        for (const order of response.data) {
          if (order.status !== 'PENDING') continue;
          if (!this.seen.has(order.id) && order.dispatch_notification_enabled !== false) hasNewOrder = true;
          this.seen.add(order.id);
        }
        if (this.initialized && hasNewOrder) orderAlertSound.play();
        this.initialized = true;
        this.subscribers.forEach(handler => handler(response));
      }
      return response;
    });
    this.inFlight = operation;
    operation.finally(() => { if (this.inFlight === operation) this.inFlight = null; }).catch(() => {});
    return operation;
  }

  onPoolUpdate(handler: (response: PoolResponse) => void) {
    this.subscribers.add(handler);
    return () => { this.subscribers.delete(handler); };
  }

  private refresh = async () => {
    if (!this.foreground || !getStoredToken() || cooldownRemaining('pool', getSessionRevision())) return;
    try { await this.loadPending(); } catch { /* Keep the last snapshot and retry on the next refresh. */ }
  };

  private authChanged = () => {
    this.syncSession();
    if (!getStoredToken()) this.stopWatching();
    else if (this.foreground) this.watch();
  };

  private visibilityChanged = () => {
    // Browser tabs may keep sounding alerts while the provider uses another tab.
    // Re-read on return in case the browser throttled timers in the background.
    if (!document.hidden && this.foreground) void this.refresh();
  };

  private watch() {
    if (this.timer !== null || !getStoredToken()) return;
    this.queue = createCoalescedRefresh(this.refresh);
    this.offSocket = [
      wsService.on('order_status_update', () => this.queue?.request()),
      wsService.on('connected', () => this.queue?.request()),
    ];
    // Covers delayed pass visibility and updates missed during a socket outage.
    this.timer = setInterval(this.refresh, 10000);
    void this.refresh();
  }

  show() {
    this.foreground = true;
    this.syncSession();
    orderAlertSound.prepare();
    if (!this.installed) {
      this.installed = true;
      Taro.eventCenter.on('xa:auth.changed', this.authChanged);
      if (process.env.TARO_ENV === 'h5') document.addEventListener('visibilitychange', this.visibilityChanged);
    }
    this.watch();
  }

  private stopWatching() {
    ++this.generation;
    this.inFlight = null;
    if (this.timer !== null) clearInterval(this.timer);
    this.timer = null;
    this.queue?.dispose();
    this.queue = null;
    this.offSocket.forEach(off => off());
    this.offSocket = [];
    orderAlertSound.stop();
  }

  hide() {
    this.foreground = false;
    this.stopWatching();
  }

  dispose() {
    this.hide();
    Taro.eventCenter.off('xa:auth.changed', this.authChanged);
    if (process.env.TARO_ENV === 'h5') document.removeEventListener('visibilitychange', this.visibilityChanged);
    this.installed = false;
    orderAlertSound.dispose();
  }
}

export const orderAlerts = new OrderAlerts();
