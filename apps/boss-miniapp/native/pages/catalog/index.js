const { request } = require('../../core/api');
const auth = require('../../core/auth');
const { money, image, list, go } = require('../../core/ui');
const { on } = require('../../core/events');
const { familyKey, groupServices, serviceTabs, serviceTabForGameplay } = require('../../core/catalog');

Page({
  data: { loading: false, error: '', logged: false, total: 0, displayed: [], displayLimit: 20, serviceTabs, tab: 'experience', categories: [], platforms: [], levels: [], category: '', platform: '', level: '', gameplay: '', gameplayName: '', keyword: '', sort: 'price', filtersExpanded: false, providerId: 0, providerName: '', couponServiceIds: null, couponName: '' },
  onLoad(options) {
    this._alive = true;
    this._requestedGameplay = options.gameplay ? String(options.gameplay) : '';
    if (serviceTabs.some(item => item.id === options.tab)) this.setData({ tab: options.tab });
    this._off = on('session.changed', () => { this._seq = (this._seq || 0) + 1; this._services = []; this._filtered = []; this.setData({ total: 0, displayed: [], couponServiceIds: null, couponName: '', logged: !!auth.getSession() }); if (this._visible) this.load(); });
  },
  onShow() {
    this._visible = true;
    const intent = wx.getStorageSync('xa:native:catalog-intent');
    if (intent && typeof intent === 'object') {
      wx.removeStorageSync('xa:native:catalog-intent');
      this._requestedGameplay = intent.gameplay ? String(intent.gameplay) : '';
      const couponServiceIds = Array.isArray(intent.service_ids) ? intent.service_ids.filter(id => Number.isSafeInteger(id) && id > 0).slice(0, 100) : null;
      this._selectAvailableTab = !!intent.provider_id || !!couponServiceIds;
      this.setData({ couponServiceIds, couponName: couponServiceIds ? String(intent.coupon_name || '指定套餐优惠券') : '', tab: serviceTabs.some(item => item.id === intent.tab) ? intent.tab : 'experience', providerId: Number(intent.provider_id) || 0, providerName: intent.provider_name || '', category: '', platform: intent.platform || '', level: intent.level || '', gameplay: this._requestedGameplay, gameplayName: '', keyword: String(intent.keyword || '').slice(0, 80) });
    }
    this.load();
  },
  onHide() { this._visible = false; },
  onUnload() { this._alive = false; this._off(); },
  async onPullDownRefresh() { await this.load(); wx.stopPullDownRefresh(); },
  async load() {
    const seq = this._seq = (this._seq || 0) + 1;
    const logged = !!auth.getSession();
    this.setData({ logged, loading: logged, error: '' });
    if (!logged) { this._services = []; this._filtered = []; this.setData({ total: 0, displayed: [] }); return; }
    try {
      const results = await Promise.all([request('/orders/services/'), request('/orders/gameplays/'), request('/orders/game-categories/'), this.data.providerId ? request('/users/escorts/') : Promise.resolve([])]);
      if (!this._alive || seq !== this._seq) return;
      const provider = list(results[3]).find(item => item.id === this.data.providerId);
      if (this.data.providerId && !provider) throw new Error('该陪玩当前不可接单，请重新选择陪玩');
      const gameplays = new Map(list(results[1]).map(item => [String(item.id), item]));
      const services = list(results[0]).filter(item => !item.is_gift && (!this.data.couponServiceIds || this.data.couponServiceIds.includes(item.id)) && (!provider || list(provider.service_item_ids).includes(item.id))).map(item => {
        const row = {};
        for (const key of ['id', 'name', 'description', 'price', 'price_unit', 'purchase_mode', 'is_addon', 'catalog_family', 'catalog_variant', 'gameplay', 'gameplay_name', 'game_category', 'game_category_name', 'platform', 'platform_name', 'security_level', 'security_level_name']) row[key] = item[key];
        return { ...row, serviceTab: serviceTabForGameplay(gameplays.get(String(item.gameplay)), item), cover: image(item.cover_url || item.gameplay_cover_url), displayPrice: money(item.price) };
      });
      const unique = (key, label) => Array.from(new Map(services.filter(item => item[key]).map(item => [item[key], { id: String(item[key]), name: item[label] || item[key] }])).values());
      this._services = services;
      const gameplay = this._requestedGameplay || this.data.gameplay;
      let tab = gameplay ? serviceTabForGameplay(gameplays.get(gameplay)) : this.data.tab;
      const selectAvailableTab = this._selectAvailableTab;
      this._requestedGameplay = ''; this._selectAvailableTab = false;
      const categories = list(results[2]).filter(item => (!provider && !this.data.couponServiceIds && !gameplay) || services.some(service => String(service.game_category) === String(item.id) && (!gameplay || String(service.gameplay) === gameplay))).map(item => ({ ...item, id: String(item.id), iconUrl: image(item.icon_url) || '/assets/icons/headphones.png', name: item.name === '无畏契约' ? '瓦罗兰特' : item.name }));
      const category = categories.some(item => item.id === this.data.category) ? this.data.category : selectAvailableTab && categories.length === 1 ? categories[0].id : '';
      const inCategory = services.filter(item => !item.is_addon && (!category || String(item.game_category) === category));
      if (selectAvailableTab && !inCategory.some(item => item.serviceTab === tab)) tab = serviceTabs.find(entry => inCategory.some(item => item.serviceTab === entry.id))?.id || tab;
      this.filter({ tab, category, categories, gameplay, gameplayName: (gameplays.get(gameplay) || {}).name || (gameplay ? '指定玩法' : ''), platforms: unique('platform', 'platform_name'), levels: unique('security_level', 'security_level_name'), loading: false, providerName: provider ? provider.nickname : '' });
    } catch (error) { if (this._alive && seq === this._seq) { this._services = []; this._filtered = []; this.setData({ loading: false, error: error.message, total: 0, displayed: [] }); } }
  },
  filter(patch = {}) {
    let { tab, category, platform, level, keyword, sort, categories, gameplay } = { ...this.data, ...patch };
    const services = this._services || [];
    const gameName = (categories.find(item => item.id === category) || {}).name || '';
    const availableTabs = /瓦罗兰特|英雄联盟/.test(gameName) ? serviceTabs.filter(item => item.id === 'companion') : serviceTabs;
    if (!availableTabs.some(item => item.id === tab)) tab = availableTabs[0].id;
    const query = keyword.trim().toLowerCase();
    const candidates = services.filter(item => (gameplay ? String(item.gameplay) === gameplay : query ? true : item.serviceTab === tab) && (!category || String(item.game_category) === category) && (!platform || item.platform === platform) && (!level || item.security_level === level));
    const matches = new Set(candidates.filter(item => !query || [item.name, item.description, item.catalog_family, item.catalog_variant, item.gameplay_name, item.game_category_name].join(' ').toLowerCase().includes(query)).map(familyKey));
    const visible = groupServices(candidates.filter(item => matches.has(familyKey(item))));
    if (sort === 'price') visible.sort((a, b) => Number(a.price) - Number(b.price));
    this._filtered = visible;
    this.setData({ ...patch, tab, serviceTabs: availableTabs, total: visible.length, displayed: visible.slice(0, 20), displayLimit: 20 });
  },
  onReachBottom() { this.more(); },
  more() {
    const rows = this._filtered || [], start = this.data.displayed.length;
    if (start >= rows.length) return;
    const next = rows.slice(start, start + 20), patch = { displayLimit: start + next.length };
    next.forEach((item, index) => { patch['displayed[' + (start + index) + ']'] = item; });
    this.setData(patch);
  },
  toggleFilters() { this.setData({ filtersExpanded: !this.data.filtersExpanded }); },
  chooseTab(event) { const id = event.currentTarget.dataset.id; if (!serviceTabs.some(item => item.id === id)) return; this._requestedGameplay = ''; this.filter({ tab: id, gameplay: '', gameplayName: '', keyword: '' }); },
  choose(event) { const { field, id } = event.currentTarget.dataset; if (!['category', 'platform', 'level', 'sort'].includes(field)) return; const value = field === 'category' && this.data.category === id ? '' : id || ''; this.filter({ [field]: value, ...(field === 'category' ? { platform: '', level: '' } : {}) }); },
  search(event) { this.setData({ keyword: event.detail.value }); this.filter(); },
  reset() { this.filter({ category: '', platform: '', level: '', keyword: '', sort: 'price' }); },
  clearGameplay() { this._requestedGameplay = ''; this.setData({ gameplay: '', gameplayName: '' }); this.load(); },
  clearCoupon() { this.setData({ couponServiceIds: null, couponName: '' }); this.load(); },
  clearProvider() { this.setData({ providerId: 0, providerName: '' }); this.load(); },
  open(event) { const provider = this.data.providerId ? '&provider_id=' + this.data.providerId : ''; go('/pages/service/index?id=' + event.currentTarget.dataset.id + provider); },
  login() { auth.ensure(); }
});
