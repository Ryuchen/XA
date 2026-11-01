const { request } = require('../../core/api');
const auth = require('../../core/auth');
const { present } = require('../../core/orders');
const { on } = require('../../core/events');
const { go } = require('../../core/ui');
Page({
  data: { logged: false, loading: false, error: '', items: [], filter: '', onlyGifts: false, tabs: [{ value: '', name: '全部' }, { value: 'pending', name: '待接单' }, { value: 'grabbed', name: '已接单' }, { value: 'in_progress', name: '服务中' }, { value: 'completed', name: '已完成' }, { value: 'cancelled', name: '已取消' }] },
  onLoad() {
    this._alive = true;
    this._off = on('session.changed', () => { this._seq++; clearTimeout(this._deadlineTimer); this.setData({ items: [], logged: !!auth.getSession() }); if (this._visible) this.load(); });
    this._orderOff = on('order.changed', () => { if (this._visible) this.load(); });
  },
  onShow() {
    this._visible = true;
    const intent = wx.getStorageSync('xa:native:orders-intent');
    if (intent) { wx.removeStorageSync('xa:native:orders-intent'); this.setData({ filter: this.data.tabs.some(tab => tab.value === intent.status) ? intent.status : '', onlyGifts: intent.type === 'gift', items: [] }); }
    return this.load();
  },
  onHide() { this._visible = false; clearTimeout(this._deadlineTimer); },
  onUnload() { this._alive = false; clearTimeout(this._deadlineTimer); this._off(); this._orderOff(); },
  onPullDownRefresh() { return this.load().finally(() => wx.stopPullDownRefresh()); },
  scheduleDeadlineRefresh(items) {
    clearTimeout(this._deadlineTimer);
    if (!this._visible) return;
    const deadlines = items.filter(item => item.status === 'PENDING' && item.payment_status === 'PAID'
      && !item.pending_deadline_expired && !item.directTest)
      .map(item => Date.parse(item.auto_cancel_at || '')).filter(Number.isFinite);
    if (!deadlines.length) return;
    // The clock only schedules a read; the server determines whether a deadline has passed.
    const remaining = Math.min(...deadlines) - Date.now();
    const delay = remaining <= 0 ? 60000 : Math.min(Math.max(remaining + 500, 1000), 60000);
    this._deadlineTimer = setTimeout(() => { if (this._alive && this._visible) this.load(); }, delay);
  },
  async load() {
    clearTimeout(this._deadlineTimer);
    const seq = this._seq = (this._seq || 0) + 1, logged = !!auth.getSession();
    this.setData({ logged, loading: logged, error: '' });
    if (!logged) return;
    try { const orders = await request('/orders/orders/?status=' + (this.data.filter || 'all')); if (this._alive && seq === this._seq) { const items = orders.map(present).filter(item => !this.data.onlyGifts || item.isGift); this.setData({ items }); this.scheduleDeadlineRefresh(items); } }
    catch (error) { if (this._alive && seq === this._seq) this.setData({ error: error.message }); }
    finally { if (this._alive && seq === this._seq) this.setData({ loading: false }); }
  },
  filter(event) { this.setData({ filter: event.currentTarget.dataset.value, items: [] }); this.load(); },
  toggleGifts() { this.setData({ onlyGifts: !this.data.onlyGifts, items: [] }); this.load(); },
  open(event) { go('/pages/order-detail/index?id=' + event.currentTarget.dataset.id); },
  catalog() { go('/pages/catalog/index'); },
  browse() { go(this.data.onlyGifts ? '/pages/gifts/index' : '/pages/catalog/index'); },
  login() { auth.ensure(); },
});
