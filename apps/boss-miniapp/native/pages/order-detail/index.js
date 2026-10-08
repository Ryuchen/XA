const { request } = require('../../core/api');
const auth = require('../../core/auth');
const { present } = require('../../core/orders');
const { on, emit } = require('../../core/events');
const { go, toast, date } = require('../../core/ui');
Page({
  data: { logged: false, loading: false, error: '', order: null, busy: false, score: 5, content: '', anonymous: false, gifts: [], giftIndex: 0, giftError: '' },
  onLoad(options) {
    this._id = Number(options.id) || 0; this._alive = true;
    this._off = on('session.changed', () => { this._seq++; this.setData({ order: null, gifts: [], content: '' }); if (this._visible) this.load(); });
    this._orderOff = on('order.changed', () => { if (this._visible && !this.data.busy) this.load(); });
  },
  onShow() { this._visible = true; this.load(); },
  onHide() { this._visible = false; },
  onUnload() { this._alive = false; this._off(); this._orderOff(); },
  onPullDownRefresh() { return this.load().finally(() => wx.stopPullDownRefresh()); },
  async load() {
    const seq = this._seq = (this._seq || 0) + 1, logged = !!auth.getSession();
    this.setData({ logged, loading: logged, error: '' });
    if (!logged) return;
    try {
      if (!this._id) throw new Error('缺少订单编号');
      const order = await request('/orders/orders/' + this._id + '/');
      if (!this._alive || seq !== this._seq) return;
      this.setData({ order: Object.assign(present(order), { aftersaleDeadlineText: date(order.aftersale_deadline), consumptionReadyText: date(order.consumption_ready_at), canAfterSale: order.status === 'COMPLETED' && order.payment_status === 'PAID' && !order.aftersale_status && !!order.aftersale_deadline && Date.parse(order.aftersale_deadline) > Date.now() }) });
    } catch (error) { if (this._alive && seq === this._seq) this.setData({ error: error.message, order: null }); }
    finally { if (this._alive && seq === this._seq) this.setData({ loading: false }); }
  },
  async operate(action) {
    if (!auth.ensure() || this.data.busy || !this.data.order) return;
    this.setData({ busy: true });
    try { await action(); if (this._alive) { emit('order.changed'); await this.load(); } }
    catch (error) { if (this._alive) toast(error); }
    finally { if (this._alive) this.setData({ busy: false }); }
  },
  selectProvider(event) {
    if (!this.data.order.canSelectProvider || this.data.busy) return;
    const row = this.data.order.applications.find(item => item.id === Number(event.currentTarget.dataset.id));
    if (!row || !row.ready) return;
    const revision = auth.revision();
    return this.operate(async () => {
      const answer = await new Promise(resolve => wx.showModal({ title: '选择本次陪玩', content: row.members.map(member => member.nickname + ' · ' + member.level).join(' + ') + '，共' + row.team_size + '人。按本单已付总金额提供服务。', confirmText: '确认选择', success: resolve, fail: () => resolve({ confirm: false }) }));
      if (answer.confirm && this._alive && revision === auth.revision()) { await request('/orders/orders/' + this._id + '/applications/' + row.id + '/select/', 'POST'); toast('已选择陪玩'); }
    });
  },
  cancel() {
    if (!this.data.order.canCancel || this.data.busy) return;
    return this.operate(async () => {
      const answer = await new Promise(resolve => wx.showModal({ title: '取消订单', content: '订单仍待接单时可以取消，成功后余额原路退回。', editable: true, placeholderText: '可填写取消原因', confirmText: '确认取消', success: resolve, fail: () => resolve({ confirm: false }) }));
      if (answer.confirm && this._alive) { await request('/orders/orders/' + this._id + '/cancel/', 'POST', { reason: (answer.content || '').slice(0, 255) }); toast('订单已取消，请查看退款流水'); }
    });
  },
  score(event) { this.setData({ score: Number(event.detail.value) }); },
  content(event) { this.setData({ content: event.detail.value }); },
  anonymous(event) { this.setData({ anonymous: event.detail.value }); },
  evaluate() {
    if (!this.data.order.canEvaluate) return;
    return this.operate(async () => { await request('/orders/evaluate/', 'POST', { order_id: this._id, score: this.data.score, content: this.data.content.trim(), is_anonymous: this.data.anonymous }); toast('评价已提交'); });
  },
  tip() {
    if (!this.data.order.canTip || this.data.busy) return;
    go('/pages/gifts/index?order_id=' + this._id);
  },
  aftersale() {
    if (!this.data.order.canAfterSale || this.data.busy) return;
    return this.operate(async () => {
      const answer = await new Promise(resolve => wx.showModal({ title: '提交订单售后', content: '请说明问题，客服处理完成前暂停确认实销。', editable: true, placeholderText: '至少2字问题说明', success: resolve, fail: () => resolve({ confirm: false }) }));
      if (answer.confirm && this._alive) { await request('/orders/orders/' + this._id + '/aftersale/', 'POST', { reason: (answer.content || '').trim() }); toast('售后已提交'); }
    });
  },
  copy() { wx.setClipboardData({ data: this.data.order.order_no }); },
  support() { go('/pages/support/index?order_id=' + this._id); },
  wallet() { go('/pages/wallet/index'); },
  login() { auth.ensure(); },
});
