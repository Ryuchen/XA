const { request, fund } = require('../../core/api');
const auth = require('../../core/auth');
const { money, image, toast, go } = require('../../core/ui');
const { on } = require('../../core/events');
Page({
  data: { logged: false, loading: false, error: '', quoteError: '', quoting: false, submitting: false, service: null, quote: null, dispatchModes: ['老板选人', '直接抢单'], dispatchIndex: 0, selfOrderEnabled: true, trafficMode: '', rounds: 1, region: '', nickname: '', uid: '', remark: '', provider: null, genderOptions: ['不限', '男陪', '女陪'], genderIndex: 0, recent: [], recommended: [], shortcutsLoading: false, shortcutsError: '', coupons: [], couponIndex: 0, contacts: [], contactIndex: 0 },
  onLoad(options) {
    this._alive = true; this._serviceId = Number(options.service_id || options.serviceId) || 0; this._providerId = Number(options.provider_id || options.playerId) || 0;
    this._contactId = Number(options.support_contact_id || options.supportContactId) || 0;
    this.setData({ genderIndex: Math.max(0, ['ANY', 'MALE', 'FEMALE'].indexOf(options.provider_gender || 'ANY')) });
    this._rounds = Math.max(1, Math.min(99, Number(options.game_rounds) || 1));
    this._off = on('session.changed', () => { this._seq++; this._quoteSeq++; this._shortcutsSeq = (this._shortcutsSeq || 0) + 1; this.setData({ service: null, quote: null, region: '', nickname: '', uid: '', remark: '', provider: null, genderIndex: 0, recent: [], recommended: [], shortcutsError: '', coupons: [], contacts: [] }); if (this._visible) this.load(); });
  },
  onShow() { this._visible = true; if (!this.data.service) this.load(); else this.refreshQuote(); },
  onHide() { this._visible = false; },
  onUnload() { this._alive = false; clearTimeout(this._debounce); this._off(); },
  onPullDownRefresh() { return this.load().finally(() => wx.stopPullDownRefresh()); },
  async load() {
    const seq = this._seq = (this._seq || 0) + 1, logged = !!auth.getSession();
    this.setData({ logged, loading: logged, error: '' });
    if (!logged) return;
    if (!this._serviceId) { this.setData({ loading: false, error: '缺少服务编号，请重新选择服务' }); return; }
    try {
      const [service, me, coupons, contacts, providers, traffic] = await Promise.all([request('/orders/services/' + this._serviceId + '/'), request('/users/me/'), request('/coupons/mine/?usable=1&service_id=' + this._serviceId), request('/support/contact-cards/'), this._providerId ? request('/users/escorts/?service_item=' + this._serviceId) : Promise.resolve([]), request('/orders/traffic-status/')]);
      if (!this._alive || seq !== this._seq) return;
      if (service.is_gift) throw new Error('礼物请从已完成订单的打赏入口赠送');
      const profile = (me.game_profiles || []).find(item => item.game_category === service.game_category) || {};
      const provider = providers.find(item => item.id === this._providerId) || null;
      if (this._providerId && !provider) throw new Error('所选陪玩当前不可接该服务，请返回重新选择');
      this.setData({ selfOrderEnabled: traffic.self_order_enabled, trafficMode: ({peak:'波峰',valley:'波谷',normal:'常规'})[traffic.mode] || '', service: { ...service, cover: image(service.cover_url || service.gameplay_cover_url), priceText: money(service.price) }, region: profile.region || '', nickname: profile.nickname || '', uid: profile.uid || '', provider,
        coupons: [...(['NEW_VALUE','DAILY_VALUE'].includes(service.experience_kind) ? [] : [{ id: 0, name: service.experience_kind ? '体验单专价，不参与活动' : '不使用优惠券' }]), ...coupons.map(item => ({ ...item, name: item.name + (item.discount_type === 'QUALIFICATION' ? ' · 套餐资格券' : item.discount_type === 'RATE' ? (item.discount_rate === 0 ? ' · 赠送券' : ' · ' + item.discount_rate / 10 + '折') : ' · 减' + money(item.amount) + '兴安币') }))],
        contacts: [{ id: 0, name: '由平台安排客服' }, ...contacts], couponIndex: 0, contactIndex: Math.max(0, contacts.findIndex(item => item.id === this._contactId) + 1), rounds: service.experience_kind || service.gameplay_kind === 'ROULETTE' ? 1 : this._rounds });
      if (provider && this.data.genderIndex && provider.gender !== ['ANY', 'MALE', 'FEMALE'][this.data.genderIndex]) throw new Error('所选陪玩不符合男女陪选择，请返回重新选择');
      await Promise.all([this.refreshQuote(), this.loadShortcuts()]);
    } catch (error) { if (this._alive && seq === this._seq) this.setData({ error: error.message }); }
    finally { if (this._alive && seq === this._seq) this.setData({ loading: false }); }
  },
  payload() {
    const data = this.data;
    const result = { dispatch_mode: data.dispatchIndex === 1 && !data.provider ? 'AUTO_GRAB' : 'BOSS_SELECT', service_id: this._serviceId, game_rounds: Number(data.rounds), game_region: data.region.trim(), game_nickname: data.nickname.trim(), game_uid: data.uid.trim(), remark: data.remark.trim() };
    if (data.service && data.service.service_type === 'COMPANION') result.provider_gender = ['ANY', 'MALE', 'FEMALE'][data.genderIndex];
    if (data.provider) result.provider_id = data.provider.id;
    if (data.coupons[data.couponIndex] && data.coupons[data.couponIndex].id) result.user_coupon_id = data.coupons[data.couponIndex].id;
    if (data.contacts[data.contactIndex] && data.contacts[data.contactIndex].id) result.support_contact_id = data.contacts[data.contactIndex].id;
    return result;
  },
  async refreshQuote() {
    if (!this.data.service || this.data.submitting) return;
    const seq = this._quoteSeq = (this._quoteSeq || 0) + 1;
    this.setData({ quote: null, quoteError: '', quoting: true });
    try {
      const rounds = Number(this.data.rounds);
      if (!Number.isInteger(rounds) || rounds < 1 || rounds > 99) throw new Error('购买数量须为 1 至 99 的整数');
      const quote = await request('/orders/quote/', 'POST', this.payload());
      if (!this._alive || seq !== this._quoteSeq) return;
      this.setData({ quote: { ...quote, originalText: money(quote.original_amount), bossText: money(quote.boss_discount), promotionText: money(quote.promo_discount), couponText: money(quote.coupon_discount), amountText: money(quote.amount), balanceText: money(quote.wallet_balance) } });
    } catch (error) { if (this._alive && seq === this._quoteSeq) this.setData({ quoteError: error.message }); }
    finally { if (this._alive && seq === this._quoteSeq) this.setData({ quoting: false }); }
  },
  input(event) {
    if (this.data.submitting) return;
    const field = event.currentTarget.dataset.field;
    if (!['rounds', 'region', 'nickname', 'uid', 'remark'].includes(field)) return;
    this.setData({ [field]: event.detail.value });
    if (field === 'rounds') { this._quoteSeq++; this.setData({ quote: null }); clearTimeout(this._debounce); this._debounce = setTimeout(() => this.refreshQuote(), 350); }
  },
  dispatch(event) { if (this.data.submitting) return; this.setData({ dispatchIndex: Number(event.detail.value) }); this.refreshQuote(); },
  async gender(event) {
    if (this.data.submitting) return;
    const genderIndex = Number(event.detail.value);
    if (![0, 1, 2].includes(genderIndex)) return;
    const mismatch = this.data.provider && genderIndex && this.data.provider.gender !== ['ANY', 'MALE', 'FEMALE'][genderIndex];
    if (mismatch) this._providerId = 0;
    this.setData({ genderIndex, ...(mismatch ? { provider: null } : {}) });
    await Promise.all([this.refreshQuote(), this.loadShortcuts()]);
  },
  async loadShortcuts() {
    if (!this.data.service) return;
    const seq = this._shortcutsSeq = (this._shortcutsSeq || 0) + 1;
    this.setData({ shortcutsLoading: true, shortcutsError: '', recent: [], recommended: [] });
    try {
      const data = await request('/users/escorts/?shortcuts=1&service_item=' + this._serviceId + '&gender=' + (this.data.service.service_type === 'COMPANION' ? ['ANY', 'MALE', 'FEMALE'][this.data.genderIndex] : 'ANY'));
      if (!this._alive || seq !== this._shortcutsSeq) return;
      const prepare = items => (items || []).slice(0, 4).map(item => ({ ...item, avatarUrl: image(item.avatar), initial: (item.nickname || '陪').slice(0, 1), genderText: ({MALE:'男陪',FEMALE:'女陪'})[item.gender] || '' }));
      this.setData({ recent: prepare(data.recent), recommended: prepare(data.recommended) });
    } catch (error) { if (this._alive && seq === this._shortcutsSeq) this.setData({ shortcutsError: error.message }); }
    finally { if (this._alive && seq === this._shortcutsSeq) this.setData({ shortcutsLoading: false }); }
  },
  selectShortcut(event) {
    if (this.data.submitting) return;
    const provider = [...this.data.recent, ...this.data.recommended].find(item => item.id === Number(event.currentTarget.dataset.id));
    if (!provider || !provider.can_order) { toast('该陪玩暂不可接此单，请选择其他陪玩'); return; }
    this._providerId = provider.id; this.setData({ provider }); this.refreshQuote();
  },
  coupon(event) { this.setData({ couponIndex: Number(event.detail.value) }); this.refreshQuote(); },
  contact(event) { this.setData({ contactIndex: Number(event.detail.value) }); },
  chooseProvider() {
    if (this.data.submitting) return;
    wx.navigateTo({ url: '/pages/companion-picker/index?service_id=' + this._serviceId + '&from_checkout=1&provider_gender=' + ['ANY', 'MALE', 'FEMALE'][this.data.genderIndex], events: { providerSelected: provider => { if (this._alive) { this._providerId = provider ? provider.id : 0; this.setData({ provider }); this.refreshQuote(); } } } });
  },
  async submit() {
    if (!auth.ensure() || this.data.submitting || !this.data.quote || this.data.quoting) return;
    if (!this.data.selfOrderEnabled) { toast('自主下单暂时关闭，请联系客服'); return; }
    if (!this.data.nickname.trim() || !this.data.region.trim()) { toast('请填写游戏昵称与大区'); return; }
    const payload = this.payload(), approved = this.data.quote.amount;
    this.setData({ submitting: true });
    try {
      const latest = await request('/orders/quote/', 'POST', payload);
      if (!this._alive) return;
      if (latest.amount !== approved) { throw new Error('价格或优惠已变化，请刷新报价后重新确认'); }
      if (!latest.balance_sufficient) { throw new Error('余额不足，请联系客服充值'); }
      const confirm = await new Promise(resolve => wx.showModal({ title: '确认余额支付', content: '将支付 ' + money(latest.amount) + ' 兴安币，下单后由平台安排服务。', confirmText: '确认支付', success: resolve, fail: () => resolve({ confirm: false }) }));
      if (!confirm.confirm || !this._alive) return;
      const order = await fund('/orders/create/', { ...payload, expected_amount: latest.amount });
      if (this._alive) wx.redirectTo({ url: '/pages/order-detail/index?id=' + order.id });
    } catch (error) {
      if (!this._alive) return;
      this.setData({ quoteError: error.message });
      if (error.code === 409) wx.showModal({ title: '请核对订单结果', content: error.message + '。请先在订单列表确认，避免重复购买。', showCancel: false, success: () => go('/pages/orders/index') });
      else toast(error);
    } finally { if (this._alive) this.setData({ submitting: false }); }
  },
  recharge() { go('/pages/support/index?scene=recharge'); },
  login() { auth.ensure(); },
});
