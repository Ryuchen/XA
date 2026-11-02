const { modal: themedModal } = require('../../core/ui');
const { request, fund } = require('../../core/api');
const auth = require('../../core/auth');
const giftIntent = require('../../core/gift-intent');
const { on, emit } = require('../../core/events');
const { image, money, go, toast } = require('../../core/ui');
const { brandAssets } = require('../../core/brand-assets');
Page({
  data: { ...brandAssets(), logged: false, loading: false, error: '', busy: false, gifts: [], visibleGifts: [], categories: [], category: '全部', recipients: [], selectedId: 0, recipientIndex: 0, quantity: 1, maxQuantity: 999, balance: 0, balanceText: '0', total: 0, totalText: '0', enough: false, walletActive: true, recipientName: '', sourceOrder: null, receipt: null },
  onLoad(options) {
    this._alive = true; this._seq = 0;
    this.setData({ recipientIndex: -1, pickerRecipients: [], recipientSearch: '', basket: {}, namingTitle: '', namingDays: 0, selectedCount: 0, selectedLines: [] });
    this._orderId = Number(options.order_id) || 0; this._providerId = Number(options.provider_id) || 0;
    this._off = on('session.changed', () => {
      this._seq++;
      this.setData({ gifts: [], visibleGifts: [], categories: [], category: '全部', recipients: [], pickerRecipients: [], recipientIndex: -1, recipientName: '', sourceOrder: null, selectedId: 0, quantity: 1, basket: {}, namingTitle: '', namingDays: 0, selectedCount: 0, selectedLines: [], receipt: null, busy: false, total: 0, totalText: '0', balance: 0, balanceText: '0', enough: false });
      if (this._visible) this.load();
    });
  },
  onShow() { this._visible = true; this.refreshPending(); if (!this.data.receipt) this.load(); },
  onHide() { this._visible = false; },
  onUnload() { this._alive = false; clearTimeout(this._searchTimer); this._off(); },
  async onPullDownRefresh() { if (!this.data.busy) await this.load(); wx.stopPullDownRefresh(); },
  async load() {
    this.refreshPending();
    const seq = ++this._seq, logged = !!auth.getSession();
    this.setData({ logged, loading: logged, error: '' });
    if (!logged) return;
    try {
      const browse = !this._orderId && !this._providerId;
      const query = this._orderId ? 'order_id=' + this._orderId : this._providerId ? 'provider_id=' + this._providerId : 'browse=1&search=' + encodeURIComponent(this.data.recipientSearch || '');
      const context = await request('/orders/gifts/context/?' + query);
      if (!this._alive || seq !== this._seq) return;
      const gifts = context.gifts.map(item => ({ id: item.id, name: item.name, displayName: item.name.replace(/^中秋限定礼物\s*[·・]\s*/, '').replace(/（打手冠名）/, ''), price: item.price, namingDays: item.gift_naming_days || 0, festival: item.gift_festival || '', category: item.service_category_name || '常规礼物', priceText: money(item.price), cover: image(item.cover_url), initial: item.name.slice(0, 1) }));
      const recipientId = this.data.recipients[this.data.recipientIndex]?.id;
      this.setData({ gifts, recipients: context.recipients, pickerRecipients: browse ? [{ nickname: '请选择收礼陪玩' }, ...context.recipients] : context.recipients, sourceOrder: context.source_order, browse,
        selectedId: gifts.some(item => item.id === this.data.selectedId) ? this.data.selectedId : (gifts[0]?.id || 0),
        recipientIndex: context.recipients.some(item => item.id === recipientId)
          ? context.recipients.findIndex(item => item.id === recipientId)
          : (!browse && context.recipients.length === 1 ? 0 : -1),
        balance: context.balance, balanceText: money(context.balance), walletActive: context.wallet_active,
        maxQuantity: context.max_quantity, loading: false });
      this.filterGifts(); this.recalculate();
    } catch (error) { if (this._alive && seq === this._seq) this.setData({ loading: false, error: error.message, gifts: [], enough: false }); }
  },
  filterGifts() { const categories = ['全部', ...new Set(this.data.gifts.map(item => item.category))]; const category = categories.includes(this.data.category) ? this.data.category : '全部'; this.setData({ categories, category, visibleGifts: this.data.gifts.filter(item => category === '全部' || item.category === category).map(item => ({ ...item, qty: (this.data.basket || {})[item.id] || 0 })) }); },
  category(event) { if (this.data.busy) return; this.setData({ category: event.currentTarget.dataset.name }); this.filterGifts(); const first = this.data.visibleGifts[0]; if (first && !this.data.visibleGifts.some(item => item.id === this.data.selectedId)) this.setData({ selectedId: first.id }); this.recalculate(); },
  recalculate() {
    const basket = this.data.basket || {};
    const selectedLines = this.data.gifts.filter(item => basket[item.id] > 0).map(item => ({ ...item, qty: basket[item.id], lineTotal: money(item.price * basket[item.id]) }));
    const total = selectedLines.reduce((sum, item) => sum + item.price * item.qty, 0);
    const selectedCount = selectedLines.reduce((sum, item) => sum + item.qty, 0);
    const namingDays = selectedLines.reduce((sum, item) => sum + item.namingDays * item.qty, 0);
    const recipient = this.data.recipients[this.data.recipientIndex];
    this.setData({ total, totalText: money(total), selectedLines, selectedCount, namingDays, recipientName: recipient?.nickname || '', enough: selectedCount > 0 && !!recipient && this.data.walletActive && total <= this.data.balance });
    this.filterGifts();
  },
  select(event) { this.changeGift(event, 1); },
  changeQuantity(event) { this.changeGift(event, Number(event.currentTarget.dataset.delta)); },
  changeGift(event, delta) {
    if (this.data.busy) return;
    const id = Number(event.currentTarget.dataset.id), basket = { ...(this.data.basket || {}) };
    if (!this.data.gifts.some(item => item.id === id)) return;
    if (delta > 0 && !basket[id] && Object.values(basket).filter(value => value > 0).length >= 30) { toast('一次最多选择 30 款礼物'); return; }
    basket[id] = Math.max(0, Math.min(this.data.maxQuantity, (basket[id] || 0) + delta));
    this.setData({ basket }); this.recalculate();
  },
  naming(event) { if (!this.data.busy) this.setData({ namingTitle: event.detail.value }); },
  recipient(event) { if (this.data.busy) return; const index = Number(event.detail.value) - (this.data.browse ? 1 : 0); this.setData({ recipientIndex: this.data.recipients[index] ? index : -1 }); this.recalculate(); },
  chooseRecipient(event) { if (this.data.busy || this.data.loading) return; const index = Number(event.currentTarget.dataset.index); if (!this.data.recipients[index]) return; this.setData({ recipientIndex: index }); this.recalculate(); },
  searchRecipient(event) { if (this.data.busy) return; this.setData({ recipientSearch: event.detail.value }); clearTimeout(this._searchTimer); this._searchTimer = setTimeout(() => { if (this._alive) this.load(); }, 400); },
  quantity(event) { if (this.data.busy) return; this.setData({ quantity: Math.max(1, Math.min(this.data.maxQuantity, this.data.quantity + Number(event.currentTarget.dataset.delta))) }); this.recalculate(); },
  refreshPending() {
    const session = auth.getSession();
    if (!session) { this.setData({ pendingGift: false, pendingBlocked: false }); return null; }
    try {
      const intent = giftIntent.load(session.user.id);
      this.setData({ pendingGift: !!intent, pendingBlocked: !!(intent && intent.blocked) });
      return intent;
    } catch (_) { this.setData({ pendingGift: true, pendingBlocked: true }); return { blocked: true }; }
  },
  async sendIntent(intent, accountId, revision) {
    const firstAttempt = !intent.attempted;
    intent.attempted = true;
    if (!giftIntent.update(accountId, intent)) throw new Error('待核对礼物已变化，请重新查看');
    try {
      const order = await fund(intent.path, intent.payload, { clientRequestId: intent.requestId });
      this.finishIntent(intent, accountId, revision, order);
    } catch (error) {
      if (revision !== auth.revision()) return;
      const definitive = error.code >= 400 && error.code < 500 && error.code !== 409 && error.code !== 429 && error.status !== 0;
      if (firstAttempt && definitive) giftIntent.clear(accountId, intent);
      else if ((!firstAttempt && definitive) || ['REQUEST_ID_CONFLICT', 'FUND_RESULT_UNAVAILABLE'].includes(error.errorCode)) { intent.blocked = true; giftIntent.update(accountId, intent); }
      if (this._alive && revision === auth.revision()) {
        this.refreshPending();
        if (this.data.pendingGift) themedModal({ title: '请先核对上次赠送', content: '上次赠送结果尚未确认。可重试原请求，或查看礼物单和钱包记录并联系客服。', showCancel: false });
        else { toast(error); if (error.errorCode === 'PRICE_CHANGED') await this.load(); }
      }
    }
  },
  finishIntent(intent, accountId, revision, order, fromLookup = false) {
    const orders = order && (Array.isArray(order.orders) ? order.orders : order.id ? [order] : []);
    if (!orders || !orders.length) throw new Error('无法确认礼物回执，请联系客服');
    if (!giftIntent.clear(accountId, intent)) { if (this._alive && revision === auth.revision()) this.refreshPending(); return; }
    if (this._alive && revision === auth.revision()) {
      const display = intent.display || {};
      this.refreshPending();
      this.setData({ receipt: { id: orders.length === 1 ? orders[0].id : 0, orderNo: orders.length === 1 ? orders[0].order_no : '已生成 ' + orders.length + ' 笔礼物单', name: display.name || '礼物赠送', recipient: display.recipient || '陪玩', quantity: order.quantity || intent.payload.quantity, amount: money(order.amount), namingDays: order.naming_days }, basket: {} });
      if (fromLookup) emit('order.changed', order);
    }
  },
  async checkPending() {
    if (!auth.ensure() || this.data.busy) return;
    const session = auth.getSession(), revision = auth.revision(), intent = this.refreshPending();
    if (!intent || !intent.path || !intent.requestId || !intent.payload) { toast('原赠送凭证不可用，请联系客服核对'); return; }
    this.setData({ busy: true });
    try {
      const result = await request('/orders/gifts/receipt/', 'POST', { path: intent.path, payload: intent.payload, client_request_id: intent.requestId });
      if (revision !== auth.revision()) return;
      if (result.status === 'COMMITTED' && result.fundResult && result.fundResult.code === 0) {
        this.finishIntent(intent, session.user.id, revision, result.fundResult.data, true);
      } else if (this._alive) {
        themedModal({ title: '暂未查到结果', content: '赠送可能仍在处理中。请保留原凭证，可稍后重试原请求或联系客服核对。', showCancel: false });
      }
    } catch (error) { if (this._alive && revision === auth.revision()) toast(error); }
    finally { if (this._alive && revision === auth.revision()) this.setData({ busy: false }); }
  },
  async recoverPending() {
    if (!auth.ensure() || this.data.busy) return;
    const session = auth.getSession(), revision = auth.revision(), intent = this.refreshPending();
    if (!intent || intent.blocked) return;
    this.setData({ busy: true });
    try { await this.sendIntent(intent, session.user.id, revision); }
    catch (error) { if (this._alive && revision === auth.revision()) { this.refreshPending(); toast(error); } }
    finally { if (this._alive && revision === auth.revision()) this.setData({ busy: false }); }
  },
  async send() {
    if (!auth.ensure() || this.data.busy || this.data.receipt || this.data.loading || this.data.error || !this.data.enough) return;
    if (this.refreshPending()) { toast('请先核对上次赠送'); return; }
    const revision = auth.revision(), seq = this._seq;
    const current = () => this._alive && auth.revision() === revision && this._seq === seq;
    const recipient = this.data.recipients[this.data.recipientIndex];
    const namingTitle = this.data.namingTitle.trim();
    if (this.data.namingDays && !namingTitle) { toast('请填写想展示的冠名'); return; }
    const payload = { items: this.data.selectedLines.map(item => ({ gift_service_id: item.id, quantity: item.qty })), provider_id: recipient.id, expected_amount: this.data.total, naming_title: namingTitle };
    this.setData({ busy: true });
    try {
      const answer = await new Promise(resolve => themedModal({ title: '确认赠送礼物', content: '送给 ' + recipient.nickname + '：' + this.data.selectedLines.length + ' 款礼物，共 ' + this.data.selectedCount + ' 件' + '，将扣除 ' + money(payload.expected_amount) + ' 兴安币。', confirmText: '确认赠送', success: resolve, fail: () => resolve({ confirm: false }) }));
      if (!answer.confirm || !current()) return;
      const endpoint = this._orderId ? '/orders/orders/' + this._orderId + '/tip/' : '/orders/gifts/send/';
      const intent = giftIntent.create(endpoint, payload, { name: this.data.selectedLines.map(item => item.displayName + ' × ' + item.qty).join('、'), recipient: recipient.nickname });
      const accountId = auth.getSession().user.id;
      if (!giftIntent.begin(accountId, intent)) throw new Error('请先核对上次赠送');
      await this.sendIntent(intent, accountId, revision);
    } catch (error) { if (current()) { toast(error); if (error.errorCode === 'PRICE_CHANGED') await this.load(); } }
    finally { if (this._alive && auth.revision() === revision) this.setData({ busy: false }); }
  },
  again() { this.setData({ receipt: null, quantity: 1 }); this.load(); },
  viewReceipt() { if (this.data.receipt) go(this.data.receipt.id ? '/pages/order-detail/index?id=' + this.data.receipt.id : '/pages/orders/index?type=gift'); },
  viewGiftOrders() { go('/pages/orders/index?type=gift'); },
  viewBenefits() { go('/pages/points/index?tab=claims'); },
  support() { go('/pages/support/index' + (this._orderId ? '?order_id=' + this._orderId : '')); },
  login() { auth.ensure(); }
});
