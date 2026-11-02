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
  private seen = new Map<number, number>();
  private assignments = new Set<number>();
  private assignmentCursor: number | null = null;
  private assignmentQueue: ReturnType<typeof createCoalescedRefresh> | null = null;
  private inFlight: Promise<PoolResponse> | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private queue: ReturnType<typeof createCoalescedRefresh> | null = null;
  private offSocket: Array<() => void> = [];
  private subscribers = new Set<(response: PoolResponse) => void>();

  soundEnabled() { return orderAlertSound.isEnabled(); }
  setSoundEnabled(enabled: boolean) { orderAlertSound.setEnabled(enabled); }

  private syncSession() {
    const revision = getSessionRevision();
    if (this.revision === revision) return;
    this.revision = revision;
    this.initialized = false;
    this.seen.clear();
    this.assignments.clear();
    this.assignmentCursor = null;
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
          const rejection = order.reject_count ?? 0;
          const previous = this.seen.get(order.id);
          // A public order can be grabbed and rejected between two reads. Its
          // ID stays the same, but the new rejection is another opportunity.
          if ((previous === undefined || rejection > previous) && order.dispatch_notification_enabled !== false) hasNewOrder = true;
          this.seen.set(order.id, rejection);
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
    else if (this.foreground) { this.watch(); this.assignmentQueue?.request(); }
  };

  private reconcileAssignments = async () => {
    if (!this.foreground || !getStoredToken()) return;
    const revision = getSessionRevision(), generation = this.generation, after = this.assignmentCursor;
    try {
      const res = await request<ApiResponse<{ latest_id: number; pending: boolean }>>(
        `/messages/assignment-alerts/${after === null ? '' : `?after=${after}`}`, 'GET');
      if (!this.foreground || generation !== this.generation || revision !== getSessionRevision()
          || res.code !== 0 || !res.data || !Number.isSafeInteger(res.data.latest_id) || res.data.latest_id < 0) return;
      const current = this.assignmentCursor;
      if (after !== null && res.data.pending && res.data.latest_id > (current ?? after)) orderAlertSound.play();
      this.assignmentCursor = Math.max(current ?? 0, res.data.latest_id);
    } catch { /* Retry on reconnect; keep the last confirmed cursor. */ }
  };

  private visibilityChanged = () => {
    // Browser tabs may keep sounding alerts while the provider uses another tab.
    // Re-read on return in case the browser throttled timers in the background.
    if (!document.hidden && this.foreground) void this.refresh();
  };

  private watch() {
    if (this.timer !== null || !getStoredToken()) return;
    this.queue = createCoalescedRefresh(this.refresh);
    this.assignmentQueue = createCoalescedRefresh(this.reconcileAssignments);
    this.offSocket = [
      wsService.on('order_status_update', () => this.queue?.request()),
      wsService.on('site_message_update', data => {
        const id = (data as { assignment_message_id?: unknown } | null)?.assignment_message_id;
        if (!this.foreground || !getStoredToken() || !Number.isSafeInteger(id) || (id as number) <= 0
            || this.assignments.has(id as number) || (this.assignmentCursor !== null && (id as number) <= this.assignmentCursor)) return;
        this.assignments.add(id as number);
        this.assignmentCursor = Math.max(this.assignmentCursor ?? 0, id as number);
        orderAlertSound.play();
      }),
      wsService.on('connected', () => { this.queue?.request(); this.assignmentQueue?.request(); }),
    ];
    // Covers delayed pass visibility and updates missed during a socket outage.
    this.timer = setInterval(this.refresh, 10000);
    void this.refresh();
    void this.reconcileAssignments();
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
    this.assignmentQueue?.dispose();
    this.assignmentQueue = null;
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
