const { request } = require('../../core/api');
const auth = require('../../core/auth');
const { go, toast } = require('../../core/ui');
const { on } = require('../../core/events');
const { prepareCompanion } = require('../../core/companion');

Page({
  data: { logged: false, loading: false, error: '', provider: null, serviceId: 0, serviceName: '', fromCheckout: false, gender: 'ANY', playing: false },
  onLoad(options) {
    this._alive = true;
    this._id = Number(options.id) || 0;
    this.setData({ serviceId: Number(options.service_id) || 0, fromCheckout: options.from_checkout === '1', gender: ['ANY', 'MALE', 'FEMALE'].includes(options.provider_gender) ? options.provider_gender : 'ANY' });
    this._off = on('session.changed', () => {
      this.stopVoice(); this._seq = (this._seq || 0) + 1;
      this.setData({ provider: null, serviceName: '' });
      if (this._visible) this.load();
    });
  },
  onShow() { this._visible = true; this._selecting = false; this.load(); },
  onHide() { this._visible = false; this.stopVoice(); },
  onUnload() { this.stopVoice(); this._alive = false; this._off(); if (this._audio) this._audio.destroy(); },
  async onPullDownRefresh() { await this.load(); wx.stopPullDownRefresh(); },
  async load() {
    const seq = this._seq = (this._seq || 0) + 1, revision = auth.revision();
    const logged = !!auth.getSession();
    this.stopVoice(); this.setData({ logged, loading: logged, error: '' });
    if (!logged) { this.setData({ provider: null, serviceName: '' }); return; }
    if (!Number.isSafeInteger(this._id) || this._id <= 0) { this.setData({ loading: false, error: '缺少陪玩编号，请从陪玩列表重新进入', provider: null }); return; }
    const { serviceId, gender } = this.data;
    const query = '?gender=' + gender + (serviceId ? '&service_item=' + serviceId : '');
    try {
      const [provider, service] = await Promise.all([request('/users/escorts/' + this._id + '/' + query), serviceId ? request('/orders/services/' + serviceId + '/') : Promise.resolve(null)]);
      if (!this._alive || seq !== this._seq || revision !== auth.revision()) return;
      this._loadedRevision = revision;
      this.setData({ loading: false, provider: prepareCompanion(provider), serviceName: service?.name || '' });
    } catch (error) {
      if (this._alive && seq === this._seq) this.setData({ loading: false, error: error.message, provider: null, serviceName: '' });
    }
  },
  ready() { return this._alive && !this.data.loading && !this.data.error && !!this.data.provider && this._loadedRevision === auth.revision() && auth.ensure(); },
  stopVoice() { if (this._audio) this._audio.stop(); if (this._alive) this.setData({ playing: false }); },
  voice() {
    if (!this.ready() || !this.data.provider.voiceUrl) return;
    if (this.data.playing) { this.stopVoice(); return; }
    if (!this._audio) {
      this._audio = wx.createInnerAudioContext();
      this._audio.onEnded(() => { if (this._alive) this.setData({ playing: false }); });
      this._audio.onError(() => { if (this._alive) { this.setData({ playing: false }); toast('语音暂时无法播放，请稍后重试'); } });
    }
    this._audio.src = this.data.provider.voiceUrl; this._audio.play(); this.setData({ playing: true });
  },
  gift() { if (!this.ready()) return; this.stopVoice(); go('/pages/gifts/index?provider_id=' + this.data.provider.id); },
  select() {
    if (!this.ready() || this._selecting) return;
    const provider = this.data.provider;
    if (!provider.can_order) { toast(provider.order_unavailable_reason || '该陪玩暂不可接单，请稍后再约'); return; }
    this.stopVoice(); this._selecting = true;
    if (this.data.fromCheckout) {
      this.getOpenerEventChannel().emit('providerSelected', provider);
      wx.navigateBack({ delta: 2, fail: () => { this._selecting = false; toast('返回订单失败，请返回后重新选择'); } });
    } else if (this.data.serviceId) {
      this.navigateSelection('/pages/checkout/index?service_id=' + this.data.serviceId + '&provider_id=' + provider.id + '&provider_gender=' + this.data.gender);
    } else {
      wx.setStorageSync('xa:native:catalog-intent', { provider_id: provider.id, provider_name: provider.nickname }); this.navigateSelection('/pages/catalog/index');
    }
  },
  navigateSelection(url) { wx.navigateTo({ url, fail: () => { this._selecting = false; toast('页面打开失败，请稍后重试'); } }); },
  login() { auth.ensure(); },
  onShareAppMessage() { return { title: this.data.provider ? this.data.provider.nickname + ' · 兴安电竞陪玩' : '兴安电竞陪玩', path: '/pages/companion-detail/index?id=' + this._id }; }
});
