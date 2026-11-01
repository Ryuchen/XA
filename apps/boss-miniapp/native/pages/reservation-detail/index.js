const { privatePage } = require('../../core/page');
const { request, fund } = require('../../core/api');
const auth = require('../../core/auth');
const events = require('../../core/events');
const { present } = require('../../core/reservations');
const { go, toast, money, modal } = require('../../core/ui');
const page = privatePage({
  data: {reservation: null, quote: null, quoteError: '', busy: false},
  init(options) { this._id = Number(options.id); },
  async fetch() {
    if (!Number.isSafeInteger(this._id) || this._id < 1) throw new Error('缺少预约编号');
    const reservation = present(await request('/orders/reservations/' + this._id + '/'));
    // A message can arrive while a price is being reviewed. Keep the quote
    // only while the appointment remains convertible; conversion rechecks it.
    return {reservation, quote: reservation.canConvert ? this.data.quote : null, quoteError: reservation.canConvert ? this.data.quoteError : ''};
  },
  async cancel() {
    if (this.data.busy || !this.data.reservation?.canCancel) return;
    const revision = auth.revision(); this._seq++; this.setData({busy: true, loading: false});
    try {
      const answer = await new Promise(resolve => modal({title: '确认取消预约？', editable: true, placeholderText: '取消原因（选填）', confirmText: '确认取消', success: resolve, fail: () => resolve({confirm: false})}));
      if (!answer.confirm || !this._alive || revision !== auth.revision()) return;
      await request('/orders/reservations/' + this._id + '/cancel/', 'POST', {reason: (answer.content || '').trim()});
      if (this._alive && revision === auth.revision()) { toast('预约已取消'); await this.load(); }
    } catch (error) { if (this._alive && revision === auth.revision()) { toast(error); await this.load(); } }
    finally { if (this._alive && revision === auth.revision()) { this.setData({busy: false}); this.refreshPending(); } }
  },
  async quote() {
    if (this.data.busy || !this.data.reservation?.canConvert) return;
    const revision = auth.revision(); this._seq++; this.setData({busy: true, loading: false, quote: null, quoteError: ''});
    try {
      const result = await request('/orders/reservations/' + this._id + '/quote/');
      const service = await request('/orders/services/' + this.data.reservation.service.id + '/');
      if (Object.entries(result.service || {}).some(([key, value]) => service[key] !== value)) throw new Error('商品信息已变化，请重新获取报价');
      if (this._alive && revision === auth.revision()) this.setData({quote: {...result, serviceDisplay: service, amountText: money(result.amount), balanceText: money(result.wallet_balance)}});
    } catch (error) { if (this._alive && revision === auth.revision()) this.setData({quoteError: error.message}); }
    finally { if (this._alive && revision === auth.revision()) { this.setData({busy: false}); this.refreshPending(); } }
  },
  async convert() {
    const quote = this.data.quote;
    if (this.data.busy || !this.data.reservation?.canConvert || !quote?.balance_sufficient) return;
    const revision = auth.revision(); this._seq++; this.setData({busy: true, loading: false});
    try {
      const service = quote.serviceDisplay;
      const description = [service.name, service.platform_name, service.security_level && service.security_level !== 'ANY' ? service.security_level_name : '', service.provider_requirement?.label || service.required_level_name].filter(Boolean).join(' · ');
      const payment = quote.amount === 0 ? '本单应付为 0，无需扣款' : '将支付 ' + quote.amountText + ' 兴安币';
      const answer = await new Promise(resolve => modal({title: '确认转为订单', content: description + '。' + payment + '，订单会立即指派给该陪玩。', confirmText: '确认成单', success: resolve, fail: () => resolve({confirm: false})}));
      if (!answer.confirm || !this._alive || revision !== auth.revision()) return;
      const result = await fund('/orders/reservations/' + this._id + '/convert/', {expected_amount: quote.amount, expected_service: quote.service});
      if (!this._alive || revision !== auth.revision()) return;
      toast('预约已成单');
      go('/pages/order-detail/index?id=' + result.order.id);
      await this.load();
    } catch (error) {
      if (this._alive && revision === auth.revision()) {
        const message = error.status === 0 ? '结果待确认，请刷新预约查看' : (error.message || '转单未完成，请重新获取报价');
        // A failed confirmation no longer proves that the displayed amount or
        // service terms are current. Require a fresh quote before trying again.
        this.setData({quote: null, quoteError: message});
        toast(message); await this.load();
      }
    } finally { if (this._alive && revision === auth.revision()) { this.setData({busy: false}); this.refreshPending(); } }
  },
  refreshPending() { if (this._refreshPending && this._visible && !this.data.busy) { this._refreshPending = false; this.load(); } },
  order() { if (this.data.reservation?.order) go('/pages/order-detail/index?id=' + this.data.reservation.order); },
  rebook() {
    const reservation = this.data.reservation;
    if (reservation?.status !== 'EXPIRED') return;
    const provider = Number(reservation.provider?.account_id), service = Number(reservation.service?.id);
    if (Number.isSafeInteger(provider) && provider > 0 && Number.isSafeInteger(service) && service > 0) {
      go('/pages/reservation-create/index?provider_id=' + provider + '&service_id=' + service);
    } else go('/pages/catalog/index');
  },
  wallet() { go('/pages/wallet/index'); },
});
const onLoad = page.onLoad, onUnload = page.onUnload;
page.onLoad = function(options) {
  onLoad.call(this, options);
  this._noticeOff = events.on('reservation.changed', (change) => {
    if (change?.reservation_id != null && String(change.reservation_id) !== String(this._id)) return;
    if (!this._visible) return;
    if (this.data.busy) this._refreshPending = true;
    else this.load();
  });
};
page.onShow = function() { this._visible = true; if (this.data.busy) this._refreshPending = true; else { this._refreshPending = false; this.load(); } };
page.onUnload = function() { this._noticeOff?.(); onUnload.call(this); };
Page(page);
