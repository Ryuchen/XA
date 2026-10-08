const auth = require('../../core/auth');
const { request } = require('../../core/api');
const { money, date, go } = require('../../core/ui');
const { on } = require('../../core/events');
const labels = { TOPUP: '充值入账', PAY: '订单支付', REFUND: '订单退款', INCOME: '收入', WITHDRAW: '提现', DEPOSIT: '押金', REWARD: '奖励', ADJUST: '账务调整' };
Page({
  data: { logged: false, loading: false, loadingMore: false, error: '', moreError: '', balance: '', frozen: '', items: [], page: 0, hasMore: false, filter: '', filters: [{ value: '', text: '全部' }, { value: 'TOPUP', text: '充值' }, { value: 'PAY', text: '支付' }, { value: 'REFUND', text: '退款' }] },
  onLoad() { this._alive = true; this._off = on('session.changed', () => { this._seq++; this.setData({ balance: '', frozen: '', items: [] }); if (this._visible) this.load(); }); },
  onShow() { this._visible = true; this.load(); },
  onHide() { this._visible = false; },
  onUnload() { this._alive = false; this._off(); },
  onPullDownRefresh() { return this.load().finally(() => wx.stopPullDownRefresh()); },
  onReachBottom() { this.more(); },
  async load() {
    const seq = this._seq = (this._seq || 0) + 1;
    const logged = !!auth.getSession();
    this.setData({ logged, loading: logged, error: '', loadingMore: false, moreError: '' });
    if (!logged) return;
    try {
      const result = await request('/wallet/transactions/?page=1&page_size=20&tx_type=' + this.data.filter);
      if (!this._alive || seq !== this._seq) return;
      this.setData({ balance: money(result.balance), frozen: money(result.frozen_amount), items: this.format(result.transactions), page: 1, hasMore: result.total > 20 });
    } catch (error) { if (this._alive && seq === this._seq) this.setData({ error: error.message }); }
    finally { if (this._alive && seq === this._seq) this.setData({ loading: false }); }
  },
  async more() {
    if (!this.data.hasMore || this.data.loading || this.data.loadingMore) return;
    const seq = this._seq, page = this.data.page + 1;
    this.setData({ loadingMore: true, moreError: '' });
    try {
      const result = await request('/wallet/transactions/?page=' + page + '&page_size=20&tx_type=' + this.data.filter);
      if (!this._alive || seq !== this._seq) return;
      const seen = new Set(this.data.items.map(item => item.id));
      this.setData({ items: this.data.items.concat(this.format(result.transactions).filter(item => !seen.has(item.id))), page, hasMore: page * 20 < result.total });
    } catch (error) { if (this._alive && seq === this._seq) this.setData({ moreError: error.message }); }
    finally { if (this._alive && seq === this._seq) this.setData({ loadingMore: false }); }
  },
  format(items) { return (items || []).map(item => ({ ...item, title: labels[item.tx_type] || item.tx_type, time: date(item.created_at), displayAmount: (item.amount > 0 ? '+' : '') + money(item.amount), after: money(item.balance_after) })); },
  filter(event) { this.setData({ filter: event.currentTarget.dataset.value, items: [] }); this.load(); },
  recharge() { go('/pages/support/index?scene=recharge'); },
  login() { auth.ensure(); },
});
