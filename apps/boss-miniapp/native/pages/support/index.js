const { request } = require('../../core/api');
const auth = require('../../core/auth');
const { image, go, toast } = require('../../core/ui');
Page({
  data: { loading: true, error: '', contactError: '', contactDetail: '', diagnostic: false, card: null, opening: false, orderId: 0, scene: '' },
  onLoad(options) { this._alive = true; this._cardId = Number(options.card_id) || 0; let diagnostic = false; try { diagnostic = wx.getAccountInfoSync().miniProgram.envVersion === 'develop'; } catch (_) {} this.setData({ diagnostic, orderId: Number(options.order_id) || 0, scene: options.scene === 'recharge' ? 'recharge' : '' }); },
  onShow() { return this.load({ preserveContactError: this._cardRevision === auth.revision() }); },
  onUnload() { this._alive = false; },
  onPullDownRefresh() { return this.load().finally(() => wx.stopPullDownRefresh()); },
  async load(options = {}) {
    const seq = this._seq = (this._seq || 0) + 1;
    this.setData({ loading: true, error: '', card: null, ...(options.preserveContactError === true ? {} : { contactError: '', contactDetail: '' }) });
    try {
      const logged = !!auth.getSession();
      const path = logged ? '/support/contact-card/' + (this.data.orderId ? '?order_id=' + this.data.orderId : this._cardId ? '?card_id=' + this._cardId : '') : '/support/public-contact/';
      const card = await request(path, 'GET', undefined, { auth: logged });
      if (this._alive && seq === this._seq) { this._cardRevision = auth.revision(); this.setData({ card: card ? { ...card, avatar: image(card.avatar_url), qrcode: image(card.qrcode_url) } : null }); }
    } catch (error) { if (this._alive && seq === this._seq) this.setData({ error: error.message }); }
    finally { if (this._alive && seq === this._seq) this.setData({ loading: false }); }
  },
  contact() {
    const card = this.data.card;
    if (!card || this.data.loading || this.data.error || this.data.opening) return;
    if (this._cardRevision !== auth.revision()) { this.load(); return; }
    if (typeof wx.openCustomerServiceChat !== 'function') { this.setData({ contactError: '当前微信版本不支持此入口，请更新微信或使用下方平台客服。' }); return; }
    if (!card.wecom_corp_id || !card.wecom_service_url) { toast('请使用下方微信号或平台客服联系'); return; }
    this.setData({ opening: true, contactError: '', contactDetail: '' });
    const failed = error => {
      if (!this._alive) return;
      this.setData({ opening: false });
      if (error && /cancel/i.test(error.errMsg || '')) return;
      const detail = [error && error.errCode, error && (error.errMsg || error.message)].filter(value => value !== undefined && value !== '').join(' · ').slice(0, 300);
      this.setData({ contactError: '微信客服暂时无法打开，请稍后重试，或使用下方平台客服。', contactDetail: this.data.diagnostic ? detail : '' });
      console.warn('微信客服打开失败', { errCode: error && error.errCode, errMsg: error && error.errMsg });
    };
    try { wx.openCustomerServiceChat({ corpId: card.wecom_corp_id, extInfo: { url: card.wecom_service_url }, showMessageCard: true,
      sendMessageTitle: [card.customer_reference, card.order_no, this.data.scene === 'recharge' ? '钱包充值咨询' : '游戏服务咨询'].filter(Boolean).join(' · ').slice(0, 100),
      sendMessagePath: this.data.orderId ? 'pages/order-detail/index?id=' + this.data.orderId : this.data.scene === 'recharge' ? 'pages/wallet/index' : 'pages/home/index',
      fail: failed, complete: () => { if (this._alive) this.setData({ opening: false }); } }); } catch (error) { failed(error); }
  },
  copyReference() { const card = this.data.card; if (card && !this.data.loading && this._cardRevision === auth.revision() && card.customer_reference) wx.setClipboardData({ data: ['咨询编号 ' + card.customer_reference, card.order_no ? '订单号 ' + card.order_no : ''].filter(Boolean).join('，') }); },
  copy() { if (this.data.card && this.data.card.wechat_id) wx.setClipboardData({ data: this.data.card.wechat_id }); },
  preview() { if (this.data.card && this.data.card.qrcode) wx.previewImage({ urls: [this.data.card.qrcode] }); },
  chat() { if (auth.ensure()) go('/pages/chat/index' + (this.data.orderId ? '?order_id=' + this.data.orderId : '')); },
});
