const auth = require('../../core/auth');
const { request } = require('../../core/api');
const { money, date, go } = require('../../core/ui');
const { on } = require('../../core/events');
const labels = { TOPUP: '充值入账', PAY: '订单支付', REFUND: '订单退款', INCOME: '收入', WITHDRAW: '提现', DEPOSIT: '押金', REWARD: '奖励', ADJUST: '账务调整' };
Page({
  data: { logged: false, loading: false, loadingMore: false, error: '', moreError: '', balance: '', frozen: '', items: [], page: 0, hasMore: false, filter: '', filters: [{ value: '', text: '全部' }, { value: 'TOPUP', text: '充值' }, { value: 'PAY', text: '支付' }, { value: 'REFUND', text: '退款' }] },
  onLoad() {
    this._alive = true; this._seq = 0;
    this._off = on('session.changed', () => { this._seq++; this._moreIntent = null; clearTimeout(this._refreshTimer); this._refreshTimer = null; this.setData({ balance: '', frozen: '', items: [], page: 0, hasMore: false }); if (this._visible) this.load(); });
    this._walletOff = on('wallet.changed', payload => {
      if (!this._visible || !auth.getSession() || (payload?.account_id != null && String(payload.account_id) !== String(auth.getSession().user.id))) return;
      ++this._seq;
      if (!this._refreshTimer) this._refreshTimer = setTimeout(() => { this._refreshTimer = null; if (this._alive && this._visible) this.load(); }, 300);
    });
  },
  onShow() { this._visible = true; this.load(); },
  onHide() { this._visible = false; clearTimeout(this._refreshTimer); this._refreshTimer = null; },
  onUnload() { this._alive = false; this._seq++; this._off(); this._walletOff(); clearTimeout(this._refreshTimer); },
  onPullDownRefresh() { return this.load().finally(() => wx.stopPullDownRefresh()); },
  onReachBottom() { this.more(); },
  async load() {
    const seq = this._seq = (this._seq || 0) + 1;
    const logged = !!auth.getSession();
    const filter = this.data.filter;
    const oldest = this.data.items.length ? this.data.items[this.data.items.length - 1].id : null;
    const requestedPage = this._moreIntent && this._moreIntent.filter === filter ? this._moreIntent.page : 1;
    this.setData({ logged, loading: logged, error: '', loadingMore: false, moreError: '' });
    if (!logged) return;
    try {
      const result = await request('/wallet/transactions/?page=1&page_size=20&tx_type=' + filter);
      if (!this._alive || seq !== this._seq) return;
      let page = 1, total = result.total;
      const rows = this.format(result.transactions), seen = new Set(rows.map(item => item.id));
      while (page * 20 < total && (page < requestedPage || (oldest != null && !seen.has(oldest)))) {
        const next = await request('/wallet/transactions/?page=' + (page + 1) + '&page_size=20&tx_type=' + filter);
        if (!this._alive || seq !== this._seq) return;
        page++; total = next.total;
        const nextRows = this.format(next.transactions);
        if (!nextRows.length) break;
        for (const item of nextRows) if (!seen.has(item.id)) { seen.add(item.id); rows.push(item); }
      }
      this._moreIntent = null;
      this.setData({ balance: money(result.balance), frozen: money(result.frozen_amount), items: rows, page, hasMore: page * 20 < total });
    } catch (error) { if (this._alive && seq === this._seq) this.setData({ error: error.message }); }
    finally { if (this._alive && seq === this._seq) this.setData({ loading: false }); }
  },
  async more() {
    if (!this.data.hasMore || this.data.loading || this.data.loadingMore) return;
    const seq = this._seq, page = this.data.page + 1, filter = this.data.filter;
    const intent = this._moreIntent = { page, filter };
    this.setData({ loadingMore: true, moreError: '' });
    try {
      const result = await request('/wallet/transactions/?page=' + page + '&page_size=20&tx_type=' + filter);
      if (!this._alive || seq !== this._seq) return;
      const seen = new Set(this.data.items.map(item => item.id));
      this.setData({ items: this.data.items.concat(this.format(result.transactions).filter(item => !seen.has(item.id))), page, hasMore: page * 20 < result.total });
    } catch (error) { if (this._alive && seq === this._seq) this.setData({ moreError: error.message }); }
    finally { if (this._alive && seq === this._seq) { if (this._moreIntent === intent) this._moreIntent = null; this.setData({ loadingMore: false }); } }
  },
  format(items) { return (items || []).map(item => ({ ...item, title: item.tx_type === 'GIFT' ? (item.amount > 0 ? '赠送入账' : item.amount < 0 ? '赠送支出' : '赠送') : labels[item.tx_type] || item.tx_type, time: date(item.created_at), displayAmount: (item.amount > 0 ? '+' : '') + money(item.amount), after: money(item.balance_after) })); },
  filter(event) { this._moreIntent = null; this.setData({ filter: event.currentTarget.dataset.value, items: [], page: 0, hasMore: false }); this.load(); },
  recharge() { go('/pages/support/index?scene=recharge'); },
  login() { auth.ensure(); },
});
