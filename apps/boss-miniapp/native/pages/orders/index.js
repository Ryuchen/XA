const { request } = require('../../core/api');
const auth = require('../../core/auth');
const { present } = require('../../core/orders');
const { on } = require('../../core/events');
const { go } = require('../../core/ui');
Page({
  data: { logged: false, loading: false, error: '', items: [], filter: '', tabs: [{ value: '', name: '全部' }, { value: 'pending', name: '待接单' }, { value: 'grabbed', name: '已接单' }, { value: 'in_progress', name: '服务中' }, { value: 'completed', name: '已完成' }, { value: 'cancelled', name: '已取消' }] },
  onLoad() {
    this._alive = true;
    this._off = on('session.changed', () => { this._seq++; this.setData({ items: [], logged: !!auth.getSession() }); if (this._visible) this.load(); });
    this._orderOff = on('order.changed', () => { if (this._visible) this.load(); });
  },
  onShow() {
    this._visible = true;
    const intent = wx.getStorageSync('xa:native:orders-intent');
    if (intent) { wx.removeStorageSync('xa:native:orders-intent'); this.setData({ filter: this.data.tabs.some(tab => tab.value === intent.status) ? intent.status : '', items: [] }); }
    this.load();
  },
  onHide() { this._visible = false; },
  onUnload() { this._alive = false; this._off(); this._orderOff(); },
  onPullDownRefresh() { return this.load().finally(() => wx.stopPullDownRefresh()); },
  async load() {
    const seq = this._seq = (this._seq || 0) + 1, logged = !!auth.getSession();
    this.setData({ logged, loading: logged, error: '' });
    if (!logged) return;
    try { const orders = await request('/orders/orders/?status=' + (this.data.filter || 'all')); if (this._alive && seq === this._seq) this.setData({ items: orders.map(present) }); }
    catch (error) { if (this._alive && seq === this._seq) this.setData({ error: error.message }); }
    finally { if (this._alive && seq === this._seq) this.setData({ loading: false }); }
  },
  filter(event) { this.setData({ filter: event.currentTarget.dataset.value, items: [] }); this.load(); },
  open(event) { go('/pages/order-detail/index?id=' + event.currentTarget.dataset.id); },
  catalog() { go('/pages/catalog/index'); },
  login() { auth.ensure(); },
});
