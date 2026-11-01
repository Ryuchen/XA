const { modal: themedModal } = require('../../core/ui');
const { request, fund } = require('../../core/api');
const auth = require('../../core/auth');
const { money, image, toast, go } = require('../../core/ui');
const { on } = require('../../core/events');
const { prepareCompanion } = require('../../core/companion');
const { normalizeProviderIds, providerLimit, toggleProviders } = require('../../core/provider-selection');
const orderIntent = require('../../core/order-intent');
const validTimeout = value => Number.isInteger(value) && value >= 1 && value <= 1440;
const serviceTerms = ['name', 'unit_price', 'price_unit', 'service_type', 'game_category', 'gameplay', 'platform',
  'security_level', 'provider_kind', 'provider_grade', 'required_level', 'service_category',
  'purchase_mode', 'is_addon', 'experience_kind', 'allows_discount_coupons'];
const fixedRoundsFor = service => !!service.experience_kind || service.gameplay_kind === 'ROULETTE' || (providerLimit(service) === 2 && ['小时', '时', 'hour'].includes(service.price_unit));
const couponOptions = (service, coupons) => [...(['NEW_VALUE','DAILY_VALUE'].includes(service.experience_kind) ? [] : [{ id: 0, name: service.experience_kind ? '体验单专价，不参与活动' : '不使用优惠券' }]), ...coupons.map(item => ({ ...item, name: item.name + (item.discount_type === 'QUALIFICATION' ? ' · 套餐资格券' : (item.threshold > 0 ? ' · 会员优惠后满' + money(item.threshold) : ' · 无门槛') + (item.discount_type === 'RATE' ? (item.discount_rate === 0 ? ' · 赠送券' : ' · ' + item.discount_rate / 10 + '折') : ' · 减' + money(item.amount) + '兴安币')) }))];
Page({
  helpVisibility(event) { if (this._alive) this.setData({ helpOpen: !!event.detail.open }); },
  data: { helpOpen: false, logged: false, loading: false, error: '', quoteError: '', quoting: false, submitting: false, pendingOrder: false, pendingBlocked: false, pendingCanResolve: false, pendingError: '', service: null, quote: null, dispatchRoute: 'PUBLIC', dispatchModes: ['老板选人', '直接抢单'], dispatchIndex: 0, selfOrderEnabled: true, trafficMode: '', rounds: 1, region: '', nickname: '', uid: '', remark: '', provider: null, providers: [], selectedProviderIds: [], selectedNames: '', selectionLimit: 1, selectionComplete: true, genderOptions: ['不限', '男陪', '女陪'], genderIndex: 0, recent: [], recommended: [], shortcutsLoading: false, shortcutsError: '', coupons: [], couponIndex: 0, contacts: [], contactIndex: 0 },
  onLoad(options) {
    this._alive = true; this._serviceId = Number(options.service_id || options.serviceId) || 0; this._providerIds = normalizeProviderIds(options.provider_ids || options.provider_id || options.playerId); this._providerId = this._providerIds[0] || 0;
    this._contactId = Number(options.support_contact_id || options.supportContactId) || 0;
    this.setData({ genderIndex: Math.max(0, ['ANY', 'MALE', 'FEMALE'].indexOf(options.provider_gender || 'ANY')) });
    this._rounds = Math.max(1, Math.min(99, Number(options.game_rounds) || 1));
    this._off = on('session.changed', () => { this._providerIds = []; this._providerId = 0; this._seq++; this._quoteSeq++; this._shortcutsSeq = (this._shortcutsSeq || 0) + 1; this.setData({ service: null, quote: null, region: '', nickname: '', uid: '', remark: '', provider: null, providers: [], selectedProviderIds: [], selectedNames: '', selectionLimit: 1, selectionComplete: true, dispatchRoute: 'PUBLIC', dispatchIndex: 0, submitting: false, pendingOrder: false, pendingBlocked: false, pendingError: '', genderIndex: 0, recent: [], recommended: [], shortcutsError: '', coupons: [], contacts: [] }); this.refreshPending(); if (this._visible) this.load(); });
  },
  onShow() { this._visible = true; this.refreshPending(); if (!this.data.service) this.load(); else this.refreshAvailability(); },
  onHide() { this._visible = false; },
  onUnload() { this._alive = false; clearTimeout(this._debounce); this._off(); },
  onPullDownRefresh() { return this.load().finally(() => wx.stopPullDownRefresh()); },
  async load() {
    this._availabilitySeq = (this._availabilitySeq || 0) + 1;
    const seq = this._seq = (this._seq || 0) + 1, logged = !!auth.getSession();
    this.setData({ logged, loading: logged, error: '', helpOpen: false });
    if (!logged) return;
    if (!this._serviceId) { this.setData({ loading: false, error: '缺少服务编号，请重新选择服务' }); return; }
    try {
      const [service, me, coupons, contacts, providers, traffic] = await Promise.all([request('/orders/services/' + this._serviceId + '/'), request('/users/me/'), request('/coupons/mine/?usable=1&service_id=' + this._serviceId), request('/support/contact-cards/'), this._providerIds.length ? request('/users/escorts/?service_item=' + this._serviceId) : Promise.resolve([]), request('/orders/traffic-status/')]);
      if (!this._alive || seq !== this._seq) return;
      if (service.is_gift) throw new Error('礼物请从已完成订单的打赏入口赠送');
      const profile = (me.game_profiles || []).find(item => item.game_category === service.game_category) || {};
      const selected = this._providerIds.map(id => providers.find(item => item.id === id));
      if (selected.some(item => !item)) throw new Error('所选陪玩当前不可接该服务，请返回重新选择');
      if (selected.length > providerLimit(service)) throw new Error('所选陪玩人数超过该套餐人数，请重新选择');
      const fixedRounds = fixedRoundsFor(service);
      this.setData({ selfOrderEnabled: traffic.self_order_enabled, trafficMode: ({peak:'波峰',valley:'波谷',normal:'常规'})[traffic.mode] || '', service: { ...service, cover: image(service.cover_url || service.gameplay_cover_url), priceText: money(service.price), fixedRounds }, selectionLimit: providerLimit(service), region: profile.region || '', nickname: profile.nickname || '', uid: profile.uid || '',
        coupons: couponOptions(service, coupons),
        contacts: [{ id: 0, name: '由平台安排客服' }, ...contacts], couponIndex: 0, contactIndex: Math.max(0, contacts.findIndex(item => item.id === this._contactId) + 1), rounds: fixedRounds ? 1 : this._rounds });
      this.setProviders(selected);
      if (selected.some(provider => this.data.genderIndex && provider.gender !== ['ANY', 'MALE', 'FEMALE'][this.data.genderIndex])) throw new Error('所选陪玩不符合男女陪选择，请返回重新选择');
      await Promise.all([this.refreshQuote(), this.loadShortcuts()]);
    } catch (error) { if (this._alive && seq === this._seq) this.setData({ error: error.message }); }
    finally { if (this._alive && seq === this._seq) this.setData({ loading: false }); }
  },
  async refreshAvailability() {
    if (!this.data.service || this.data.submitting) return;
    const seq = this._availabilitySeq = (this._availabilitySeq || 0) + 1, revision = auth.revision();
    let quoteHandedOff = false;
    this._quoteSeq++; this.setData({ quote: null, quoteError: '', quoting: true });
    try {
      const [service, rows, traffic] = await Promise.all([
        request('/orders/services/' + this._serviceId + '/'),
        request('/coupons/mine/?usable=1&service_id=' + this._serviceId),
        request('/orders/traffic-status/')
      ]);
      if (!this._alive || !this._visible || seq !== this._availabilitySeq || revision !== auth.revision()) return;
      if (service.is_gift) throw new Error('该商品已变更，请返回目录重新选择');
      const selected = this.data.coupons[this.data.couponIndex];
      const choices = couponOptions(service, rows);
      const index = selected ? choices.findIndex(item => item.id === selected.id) : -1;
      const fixedRounds = fixedRoundsFor(service), resetRounds = fixedRounds && Number(this.data.rounds) !== 1;
      const providers = this.selectedProviders();
      this.setData({ service: { ...service, cover: image(service.cover_url || service.gameplay_cover_url), priceText: money(service.price), fixedRounds },
        selfOrderEnabled: traffic.self_order_enabled, trafficMode: ({peak:'波峰',valley:'波谷',normal:'常规'})[traffic.mode] || '',
        rounds: resetRounds ? 1 : this.data.rounds, coupons: choices, couponIndex: index < 0 ? 0 : index });
      this.setProviders(providers);
      const notices = [];
      if (selected && selected.id && index < 0) notices.push('所选优惠券已不可用');
      if (resetRounds) notices.push('套餐数量已调整为 1');
      if (notices.length) toast(notices.join('，') + '，请核对最新报价');
      quoteHandedOff = true;
      await Promise.all([this.refreshQuote(), this.loadShortcuts()]);
    } catch (error) {
      if (this._alive && this._visible && seq === this._availabilitySeq && revision === auth.revision())
        this.setData({ quoteError: error.message || '优惠券暂时无法更新，请重试' });
    } finally {
      if (!quoteHandedOff && this._alive && seq === this._availabilitySeq && revision === auth.revision()) this.setData({ quoting: false });
    }
  },
  selectedProviders() { return this.data.providers.length ? this.data.providers : this.data.provider ? [this.data.provider] : []; },
  setProviders(items) {
    const ids = normalizeProviderIds(items), providers = ids.map(id => prepareCompanion(items.find(item => Number(item.id) === id)));
    const limit = providerLimit(this.data.service);
    this._providerIds = ids; this._providerId = ids[0] || 0;
    const mark = rows => rows.map(item => ({ ...item, selected: ids.includes(item.id) }));
    this.setData({ providers, provider: providers[0] || null, selectedProviderIds: ids, selectedNames: providers.map(item => item.nickname).join('、'), selectionLimit: limit, selectionComplete: !ids.length || ids.length === limit, recent: mark(this.data.recent), recommended: mark(this.data.recommended) });
  },
  payload() {
    const data = this.data;
    const providers = this.selectedProviders(), limit = providerLimit(data.service);
    const route = providers.length ? 'PUBLIC' : data.dispatchRoute;
    const result = { dispatch_route: route, dispatch_mode: route === 'PUBLIC' && data.dispatchIndex === 1 && !providers.length && limit === 1 ? 'AUTO_GRAB' : 'BOSS_SELECT', service_id: this._serviceId, game_rounds: Number(data.rounds), game_region: data.region.trim(), game_nickname: data.nickname.trim(), game_uid: data.uid.trim(), remark: data.remark.trim() };
    if (data.service && data.service.service_type === 'COMPANION') result.provider_gender = ['ANY', 'MALE', 'FEMALE'][data.genderIndex];
    if (providers.length) { if (limit === 2) result.provider_ids = providers.map(item => item.id); else result.provider_id = providers[0].id; }
    if (data.coupons[data.couponIndex] && data.coupons[data.couponIndex].id) result.user_coupon_id = data.coupons[data.couponIndex].id;
    if (data.contacts[data.contactIndex] && data.contacts[data.contactIndex].id) result.support_contact_id = data.contacts[data.contactIndex].id;
    return result;
  },
  async refreshQuote() {
    if (!this.data.service || this.data.submitting) return;
    const seq = this._quoteSeq = (this._quoteSeq || 0) + 1;
    this.setData({ quote: null, quoteError: '', quoting: true });
    try {
      const count = this.selectedProviders().length;
      if (count && count !== this.data.selectionLimit) throw new Error('请选齐' + this.data.selectionLimit + '位陪玩，或改为平台安排');
      const rounds = Number(this.data.rounds);
      if (!Number.isInteger(rounds) || rounds < 1 || rounds > 99) throw new Error('购买数量须为 1 至 99 的整数');
      const quote = await request('/orders/quote/', 'POST', this.payload());
      if (!this._alive || seq !== this._quoteSeq) return;
      if (!count && !validTimeout(quote.pending_timeout_minutes)) throw new Error('待接单时限暂不可确认，请刷新报价');
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
  selectDispatchRoute(event) {
    const route = event.currentTarget.dataset.route;
    if (this.data.submitting || this.selectedProviders().length || !['PUBLIC', 'STAFF'].includes(route) || route === this.data.dispatchRoute) return;
    this.setData({ dispatchRoute: route }); this.refreshQuote();
  },
  async gender(event) {
    if (this.data.submitting) return;
    const genderIndex = Number(event.detail.value);
    if (![0, 1, 2].includes(genderIndex)) return;
    const providers = this.selectedProviders().filter(provider => !genderIndex || provider.gender === ['ANY', 'MALE', 'FEMALE'][genderIndex]);
    this.setData({ genderIndex }); this.setProviders(providers);
    await Promise.all([this.refreshQuote(), this.loadShortcuts()]);
  },
  async loadShortcuts() {
    if (!this.data.service) return;
    const seq = this._shortcutsSeq = (this._shortcutsSeq || 0) + 1;
    this.setData({ shortcutsLoading: true, shortcutsError: '', recent: [], recommended: [] });
    try {
      const data = await request('/users/escorts/?shortcuts=1&service_item=' + this._serviceId + '&gender=' + (this.data.service.service_type === 'COMPANION' ? ['ANY', 'MALE', 'FEMALE'][this.data.genderIndex] : 'ANY'));
      if (!this._alive || seq !== this._shortcutsSeq) return;
      const ids = this.selectedProviders().map(item => item.id);
      const prepare = items => (items || []).slice(0, 4).map(item => ({ ...item, avatarUrl: image(item.avatar), initial: (item.nickname || '陪').slice(0, 1), selected: ids.includes(item.id), genderText: ({MALE:'男陪',FEMALE:'女陪'})[item.gender] || '' }));
      this.setData({ recent: prepare(data.recent), recommended: prepare(data.recommended) });
    } catch (error) { if (this._alive && seq === this._shortcutsSeq) this.setData({ shortcutsError: error.message }); }
    finally { if (this._alive && seq === this._shortcutsSeq) this.setData({ shortcutsLoading: false }); }
  },
  selectShortcut(event) {
    if (this.data.submitting) return;
    const provider = [...this.data.recent, ...this.data.recommended].find(item => item.id === Number(event.currentTarget.dataset.id));
    if (!provider || !provider.can_order) { toast('该陪玩暂不可接此单，请选择其他陪玩'); return; }
    const result = toggleProviders(this.selectedProviders(), provider, this.data.selectionLimit);
    if (result.error) { toast(result.error); return; }
    this.setProviders(result.providers); this.refreshQuote();
  },
  coupon(event) { this.setData({ couponIndex: Number(event.detail.value) }); this.refreshQuote(); },
  contact(event) { this.setData({ contactIndex: Number(event.detail.value) }); },
  chooseProvider() {
    if (this.data.submitting) return;
    const revision = auth.revision();
    const ids = this.selectedProviders().map(item => item.id);
    const apply = providers => { if (this._alive && revision === auth.revision()) { this.setProviders(providers); this.refreshQuote(); } };
    wx.navigateTo({ url: '/pages/companion-picker/index?service_id=' + this._serviceId + '&from_checkout=1&provider_gender=' + ['ANY', 'MALE', 'FEMALE'][this.data.genderIndex] + (ids.length ? '&selected_provider_ids=' + ids.join(',') : ''), events: { providersSelected: apply, providerSelected: provider => { if (this.data.selectionLimit === 1) apply(provider ? [provider] : []); } } });
  },
  refreshPending() {
    const session = auth.getSession();
    if (!session) { this.setData({ pendingOrder: false, pendingBlocked: false, pendingCanResolve: false, pendingError: '' }); return null; }
    try {
      const intent = orderIntent.load(session.user.id);
      this.setData({ pendingOrder: !!intent, pendingBlocked: !!(intent && intent.blocked), pendingCanResolve: !!(intent && intent.requestId && intent.payload), pendingError: intent && intent.blocked ? '存在无法自动恢复的订单凭证，请先在订单及钱包记录核对并联系客服。' : '' });
      return intent;
    } catch (error) {
      this.setData({ pendingOrder: true, pendingBlocked: true, pendingCanResolve: false, pendingError: error.message });
      return { blocked: true };
    }
  },
  async sendIntent(intent, accountId, revision) {
    const firstAttempt = !intent.attempted;
    intent.attempted = true;
    if (!orderIntent.update(accountId, intent)) throw new Error('待核对订单已变化，请重新查看');
    try {
      const order = await fund('/orders/create/', intent.payload, { clientRequestId: intent.requestId });
      if (!orderIntent.clear(accountId, intent)) { if (this._alive && revision === auth.revision()) this.refreshPending(); return; }
      if (this._alive && revision === auth.revision()) { this.refreshPending(); wx.redirectTo({ url: '/pages/order-detail/index?id=' + order.id }); }
    } catch (error) {
      if (revision !== auth.revision()) return;
      // Only a definitive rejection of the first attempt proves no earlier
      // uncertain request could still commit. Keep all other results for review.
      const definitive = error.code >= 400 && error.code < 500 && error.code !== 409 && error.code !== 429 && error.status !== 0;
      if (firstAttempt && definitive) orderIntent.clear(accountId, intent);
      else if ((!firstAttempt && definitive) || ['REQUEST_ID_CONFLICT', 'FUND_RESULT_UNAVAILABLE'].includes(error.errorCode)) { intent.blocked = true; orderIntent.update(accountId, intent); }
      if (this._alive && revision === auth.revision()) {
        this.refreshPending();
        if (this.data.pendingOrder) themedModal({ title: '请先核对上次订单', content: '上次下单结果尚未确认。可重试上次下单，或先查看订单和钱包记录；请勿重复购买。', showCancel: false });
        else { this.setData({ quote: null, quoteError: error.message }); toast(error); }
      }
    }
  },
  async recoverPending() {
    if (!auth.ensure() || this.data.submitting) return;
    const session = auth.getSession(), revision = auth.revision(), intent = this.refreshPending();
    if (!intent || intent.blocked) return;
    this.setData({ submitting: true });
    try { await this.sendIntent(intent, session.user.id, revision); }
    catch (error) { if (this._alive && revision === auth.revision()) { this.refreshPending(); toast(error); } }
    finally { if (this._alive && revision === auth.revision()) this.setData({ submitting: false }); }
  },
  async resolvePending() {
    if (!auth.ensure() || this.data.submitting) return;
    const session = auth.getSession(), revision = auth.revision(), intent = this.refreshPending();
    if (!intent || !intent.requestId || !intent.payload) return;
    this.setData({ submitting: true });
    try {
      const answer = await new Promise(resolve => themedModal({ title: '核对或停止上次下单', content: '系统将核对上次请求：如已下单则打开原订单；如尚未下单则停止该请求，之后可重新购买。', confirmText: '确认核对', success: resolve, fail: () => resolve({ confirm: false }) }));
      if (!answer.confirm || !this._alive || revision !== auth.revision()) return;
      const result = await request('/orders/create/resolve/', 'POST', { client_request_id: intent.requestId, payload: { ...intent.payload, client_request_id: intent.requestId } });
      if (!this._alive || revision !== auth.revision()) return;
      if (result.status === 'COMMITTED' && result.fundResult && result.fundResult.code === 0 && result.fundResult.data && result.fundResult.data.id) {
        if (!orderIntent.clear(session.user.id, intent)) { this.refreshPending(); return; } this.refreshPending();
        wx.redirectTo({ url: '/pages/order-detail/index?id=' + result.fundResult.data.id });
      } else if (result.status === 'SEALED') {
        if (!orderIntent.clear(session.user.id, intent)) { this.refreshPending(); return; } this.refreshPending();
        toast(this.data.pendingOrder ? '该请求已停止，请继续核对其他请求' : '上次请求已停止，可重新下单');
      } else throw new Error('无法确认上次下单结果，请联系客服核对');
    } catch (error) {
      if (this._alive && revision === auth.revision()) { this.refreshPending(); themedModal({ title: '仍需核对上次订单', content: (error.message || '暂时无法确认结果') + '。请查看订单和钱包记录或联系客服，不要重复购买。', showCancel: false }); }
    } finally { if (this._alive && revision === auth.revision()) this.setData({ submitting: false }); }
  },
  async submit() {
    if (!this.data.selectionComplete) { toast('请选齐' + this.data.selectionLimit + '位陪玩，或改为平台安排'); return; }
    if (!auth.ensure() || this.data.submitting || !this.data.quote || this.data.quoting) return;
    if (!this.data.selfOrderEnabled) { toast('自主下单暂时关闭，请联系客服'); return; }
    if (!this.data.nickname.trim() || !this.data.region.trim()) { toast('请填写游戏昵称与大区'); return; }
    if (this.refreshPending()) { themedModal({ title: '请先核对上次订单', content: '请先重试上次下单或查看订单和钱包记录，再进行新的购买。', showCancel: false }); return; }
    const payload = this.payload(), approvedQuote = this.data.quote, approved = approvedQuote.amount, approvedTimeout = approvedQuote.pending_timeout_minutes, arranged = this.selectedProviders().length > 0, revision = auth.revision(), accountId = auth.getSession().user.id;
    this.setData({ submitting: true });
    try {
      // A package can change while this page remains open, even when its total does not.
      const currentService = await request('/orders/services/' + this._serviceId + '/');
      if (!this._alive || revision !== auth.revision()) return;
      const displayedService = this.data.service;
      if (providerLimit(currentService) !== providerLimit(displayedService) ||
          serviceTerms.some(key => currentService[key] !== displayedService[key])) {
        throw new Error('商品信息已变化，请刷新报价后重新确认');
      }
      const latest = await request('/orders/quote/', 'POST', payload);
      if (!this._alive || revision !== auth.revision()) return;
      const quoteFields = ['original_amount', 'boss_discount', 'promo_discount', 'coupon_discount'];
      if (latest.amount !== approved || quoteFields.some(key => latest[key] !== approvedQuote[key])) { throw new Error('价格或优惠已变化，请刷新报价后重新确认'); }
      if (!arranged && (!validTimeout(approvedTimeout) || !validTimeout(latest.pending_timeout_minutes))) { throw new Error('待接单时限暂不可确认，请刷新报价'); }
      if (!arranged && latest.pending_timeout_minutes !== approvedTimeout) { throw new Error('待接单时限已变化，请刷新报价后重新确认'); }
      if (!latest.balance_sufficient) { throw new Error('余额不足，请联系客服充值'); }
      const arrangement = arranged ? '已指定陪玩' : payload.dispatch_route === 'STAFF' ? (this.data.selectionLimit === 2 ? '由客服指定两名陪玩，不进入公开接单池' : '由客服指定陪玩，不进入公开接单池') : this.data.selectionLimit === 2 ? '公开给符合条件的陪玩组队报名，队友确认后请你在订单详情选人' : payload.dispatch_mode === 'BOSS_SELECT' ? '公开给符合条件的陪玩报名，由你在订单详情选人' : '公开给符合条件的陪玩直接接单';
      const noPayment = latest.amount === 0;
      const timeoutNotice = arranged ? '' : (noPayment ? '下单后' : '付款后') + '待接单时限为 ' + approvedTimeout + ' 分钟，到期仍未安排将自动取消' + (noPayment ? '。' : '并退回余额。');
      const confirm = await new Promise(resolve => themedModal({ title: noPayment ? '确认下单' : '确认余额支付', content: (noPayment ? '本单实付 0 兴安币。' : '将支付 ' + money(latest.amount) + ' 兴安币。') + arrangement + '。' + timeoutNotice, confirmText: noPayment ? '确认下单' : '确认支付', success: resolve, fail: () => resolve({ confirm: false }) }));
      if (!confirm.confirm || !this._alive || revision !== auth.revision()) return;
      if (this.refreshPending()) throw new Error('存在待核对订单，请先处理上次下单');
      const expectedService = { name: currentService.name, price_unit: currentService.price_unit,
        service_type: currentService.service_type, game_category: currentService.game_category,
        billing_provider_count: providerLimit(currentService), unit_price: currentService.unit_price };
      for (const key of serviceTerms) if (currentService[key] !== undefined) expectedService[key] = currentService[key];
      const expectedQuote = quoteFields.reduce((result, key) => { result[key] = latest[key]; return result; }, {});
      const intent = orderIntent.create({ ...payload, expected_service: expectedService, expected_quote: expectedQuote, expected_amount: latest.amount, ...(!arranged ? { expected_timeout_minutes: approvedTimeout } : {}) });
      if (!orderIntent.begin(accountId, intent)) throw new Error('存在待核对订单，请先处理上次下单');
      await this.sendIntent(intent, accountId, revision);
    } catch (error) {
      if (!this._alive || revision !== auth.revision()) return;
      this.setData({ quote: null, quoteError: error.message });
      if (error.code === 409) themedModal({ title: '请核对订单结果', content: error.message + '。请先在订单列表确认，避免重复购买。', showCancel: false, success: () => go('/pages/orders/index') });
      else toast(error);
    } finally { if (this._alive && revision === auth.revision()) this.setData({ submitting: false }); }
  },
  recharge() { go('/pages/support/index?scene=recharge'); },
  checkOrders() { go('/pages/orders/index'); },
  login() { auth.ensure(); },
});
