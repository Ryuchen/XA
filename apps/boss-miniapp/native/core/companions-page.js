const { request } = require('./api');
const auth = require('./auth');
const { image, list, go, toast } = require('./ui');
const { prepareCompanion } = require('./companion');
const { on } = require('./events');
const { normalizeProviderIds, providerLimit, toggleProviders, selectionReady } = require('./provider-selection');

module.exports = (options = {}) => ({
  data: { loading: false, error: '', logged: false, escorts: [], visible: [], categories: [], category: '', keyword: '', serviceId: 0, serviceName: '', fromCheckout: false, playingId: 0,
    isPicker: !!options.picker,
    selectedProviders: [], selectedProviderIds: [], selectionLimit: 1, selectionReady: true, selectionHint: '', validating: false,
    qualificationMeta: {kinds:[],platforms:[],scopes:[]}, scope: {provider_kind:'',platform:'',security_level:'',grade:''}, scopeFields: [], serviceRequirement: '',
    voiceOnly: false, genderOptions: ['不限', '男陪', '女陪'], genderIndex: 0, sort: 0, sortLabels: ['默认排序','时价低到高','时价高到低'], sortOptions: ['默认排序', '参考时价从低到高', '参考时价从高到低'] },
  onLoad(options) {
    this._alive = true;
    this._openerRevision = auth.revision();
    const selectedProviderIds = normalizeProviderIds(options.selected_provider_ids || options.provider_ids || options.provider_id);
    this.setData({ serviceId: Number(options.service_id) || 0, fromCheckout: options.from_checkout === '1', genderIndex: Math.max(0, ['ANY', 'MALE', 'FEMALE'].indexOf(options.provider_gender || 'ANY')) });
    if (this.data.isPicker && this.data.serviceId) this.updateSelection(selectedProviderIds.map(id => ({ id, nickname: '陪玩' + id, can_order: false, _selectionUnavailable: true })));
    this._off = on('session.changed', () => {
      this.stopVoice(); this._seq = (this._seq || 0) + 1; this._confirming = false;
      this.setData({ escorts: [], visible: [], validating: false }); this.updateSelection([]);
      if (this._visible) this.load();
    });
  },
  onShow() { this._visible = true; this._openingDetail = false; this._selectionCommitted = false; return this.load(); },
  onHide() { this._visible = false; this.stopVoice(); },
  onUnload() { this._alive = false; this._off(); if (this._audio) this._audio.destroy(); },
  async onPullDownRefresh() { await this.load(); wx.stopPullDownRefresh(); },
  async load() {
    const seq = this._seq = (this._seq || 0) + 1;
    const revision = auth.revision();
    const logged = !!auth.getSession();
    this.setData({ logged, loading: logged, error: '' });
    if (!logged) { this.setData({ escorts: [], visible: [] }); return; }
    const { serviceId, category } = this.data;
    const servicePicker = this.data.isPicker && serviceId;
    const scopeQuery = serviceId ? '' : Object.entries(this.data.scope).filter(([,v]) => v).map(([k,v]) => '&'+k+'='+encodeURIComponent(v)).join('');
    const query = serviceId ? '?service_item=' + serviceId + (this.data.isPicker ? '&picker=1' : '') : '?directory=1' + (category ? '&game_category=' + category : '');
    try {
      const [escorts, categories, service, qualificationMeta] = await Promise.all([request('/users/escorts/' + query + scopeQuery + (this.data.genderIndex ? '&gender=' + ['ANY', 'MALE', 'FEMALE'][this.data.genderIndex] : '')), servicePicker ? Promise.resolve([]) : request('/orders/game-categories/'), serviceId ? request('/orders/services/' + serviceId + '/') : Promise.resolve(null), servicePicker ? Promise.resolve(null) : request('/users/qualification-options/')]);
      if (!this._alive || seq !== this._seq || revision !== auth.revision()) return;
      const prepared = list(escorts).map(prepareCompanion);
      const selected = servicePicker ? await this.refreshSelected(prepared, serviceId) : [];
      if (!this._alive || seq !== this._seq || revision !== auth.revision()) return;
      this._loadedRevision = revision;
      this.setData({ loading: false, escorts: prepared, categories: list(categories).map(item => ({ ...item, id: String(item.id), iconUrl: image(item.icon_url) || '/assets/icons/headphones.png' })), qualificationMeta: qualificationMeta || {kinds:[],platforms:[],scopes:[]}, serviceRequirement: service?.provider_requirement?.label || '', serviceName: service ? service.name : '', selectionLimit: providerLimit(service) });
      if (servicePicker) this.updateSelection(selected);
      this.updateScopeFields(); this.filter();
    } catch (error) { if (this._alive && seq === this._seq) { this.stopVoice(); this.setData({ loading: false, error: error.message, escorts: [], visible: [] }); } }
  },
  filter() {
    const { voiceOnly, sort, genderIndex } = this.data;
    const query = this.data.keyword.trim().toLowerCase();
    const selectedIds = this.data.selectedProviderIds;
    const visible = this.data.escorts.filter(item => {
      if (query && ![item.nickname, item.rank, item.bio, item.city].join(' ').toLowerCase().includes(query)) return false;
      if (genderIndex && item.gender !== ['ANY', 'MALE', 'FEMALE'][genderIndex]) return false;
      if (voiceOnly && !item.voiceUrl) return false;
      return true;
    }).map(item => ({ ...item, selected: selectedIds.includes(Number(item.id)) }));
    if (sort) visible.sort((a, b) => {
      if (a.hasHourlyPrice !== b.hasHourlyPrice) return a.hasHourlyPrice ? -1 : 1;
      return sort === 1 ? Number(a.pricePerHour) - Number(b.pricePerHour) : Number(b.pricePerHour) - Number(a.pricePerHour);
    });
    if (this.data.playingId && !visible.some(item => item.id === this.data.playingId)) this.stopVoice();
    this.setData({ visible });
  },
  updateSelection(providers) {
    const selectedProviderIds = normalizeProviderIds(providers);
    const selectedProviders = selectedProviderIds.map(id => providers.find(item => Number(item.id) === id));
    const limit = this.data.selectionLimit;
    const valid = selectedProviders.every(item => item.can_order && !item._selectionUnavailable);
    const full = selectionReady(selectedProviders, limit);
    const hint = !valid ? '已选陪玩接单信息已变化，请取消后重新选择'
      : !full ? selectedProviders.length > limit ? '套餐人数已变化，请调整陪玩人数' : '还需选择' + (limit - selectedProviders.length) + '位陪玩'
      : selectedProviders.length ? '已选齐，套餐价格不变' : '未指定时由平台安排';
    this.setData({ selectedProviders, selectedProviderIds, selectionReady: valid && full, selectionHint: hint });
    this.filter();
  },
  async refreshSelected(prepared, serviceId) {
    const gender = ['ANY', 'MALE', 'FEMALE'][this.data.genderIndex];
    return Promise.all(this.data.selectedProviders.map(async item => {
      const current = prepared.find(row => Number(row.id) === Number(item.id));
      if (current) return current;
      try { return prepareCompanion(await request('/users/escorts/' + item.id + '/?gender=' + gender + '&service_item=' + serviceId)); }
      catch (_) { return { ...item, can_order: false, _selectionUnavailable: true }; }
    }));
  },
  selectionAllowed() {
    if (!this._alive || this._visible === false || this.data.loading || this.data.error || this.data.validating || this._selectionCommitted || this._loadedRevision !== auth.revision()) return false;
    if (this.data.fromCheckout && this._openerRevision !== auth.revision()) { toast('账号已变更，请返回订单重新选择'); return false; }
    return auth.ensure();
  },
  chooseProvider(event) {
    if (!this.data.serviceId) { this.openDetail(event); return; }
    if (!this.data.isPicker || !this.selectionAllowed()) return;
    const provider = this.data.escorts.find(item => Number(item.id) === Number(event.currentTarget.dataset.id));
    if (!provider) return;
    this.applyProvider(provider);
  },
  applyProvider(provider) {
    const next = toggleProviders(this.data.selectedProviders, provider, this.data.selectionLimit);
    if (next.error) toast(next.error);
    else this.updateSelection(next.providers);
  },
  removeSelected(event) {
    if (this.data.loading || this.data.validating || this._selectionCommitted) return;
    const id = Number(event.currentTarget.dataset.id);
    this.updateSelection(this.data.selectedProviders.filter(item => Number(item.id) !== id));
  },
  async confirmSelection() {
    if (!this.data.serviceId || !this.selectionAllowed() || this._confirming) return;
    if (!this.data.selectionReady) { toast(this.data.selectionHint); return; }
    this.stopVoice(); this._confirming = true; this.setData({ validating: true });
    const revision = auth.revision(), { serviceId } = this.data;
    const gender = ['ANY', 'MALE', 'FEMALE'][this.data.genderIndex];
    try {
      const [service, providers] = await Promise.all([
        request('/orders/services/' + serviceId + '/'),
        Promise.all(this.data.selectedProviders.map(async item => {
          try { return prepareCompanion(await request('/users/escorts/' + item.id + '/?gender=' + gender + '&service_item=' + serviceId)); }
          catch (_) { return { ...item, can_order: false, _selectionUnavailable: true }; }
        }))
      ]);
      if (!this._alive || this._visible === false || revision !== auth.revision()) return;
      this.setData({ selectionLimit: providerLimit(service) }); this.updateSelection(providers);
      if (!this.data.selectionReady) { toast(this.data.selectionHint); return; }
      this._selectionCommitted = true;
      if (this.data.fromCheckout) {
        if (revision !== this._openerRevision) return;
        this.getOpenerEventChannel().emit('providersSelected', providers);
        wx.navigateBack({ fail: () => { this._selectionCommitted = false; toast('返回订单失败，请返回后重新选择'); } });
      } else {
        wx.navigateTo({ url: '/pages/checkout/index?service_id=' + serviceId + '&provider_gender=' + gender
          + (providers.length ? '&provider_ids=' + normalizeProviderIds(providers).join(',') : ''),
        fail: () => { this._selectionCommitted = false; toast('订单页面打开失败，请稍后重试'); } });
      }
    } catch (error) { if (this._alive && revision === auth.revision()) { this._selectionCommitted = false; toast(error); } }
    finally { if (this._alive && revision === auth.revision()) { this._confirming = false; this.setData({ validating: false }); } }
  },
  updateScopeFields() {
    const meta=this.data.qualificationMeta, scope=this.data.scope;
    const scopes=(meta.scopes || []).filter(s => (!scope.provider_kind || s.provider_kind===scope.provider_kind) && (!scope.platform || s.platform===scope.platform));
    const securities=[...new Set(scopes.map(s=>s.security_level))].map(value=>({value,label:({ANY:'不限难度',CONFIDENTIAL:'机密',TOP_SECRET:'绝密'})[value]}));
    const grades=(meta.scopes || []).find(s=>s.provider_kind===scope.provider_kind && s.platform===scope.platform && s.security_level===scope.security_level)?.grades || [];
    const fields=[['provider_kind','服务方向',meta.kinds || []],['platform','平台',meta.platforms || []],['security_level','难度',securities],['grade','最低档位',grades]].map(([key,label,values])=>{const options=[{value:'',label:key==='provider_kind' ? '全部方向' : key==='platform' ? '端游 / 手游' : key==='security_level' ? '不限难度' : '不限档位'},...values];return {key,label,options,index:Math.max(0,options.findIndex(o=>o.value===scope[key])),disabled:key==='grade' && !grades.length};});
    this.setData({scopeFields:fields,hasScope:!!Object.values(scope).some(Boolean)});
  },
  chooseScope(event) {
    const field=this.data.scopeFields.find(f=>f.key===event.currentTarget.dataset.key), index=Number(event.detail.value);
    if(!field || !field.options[index] || field.disabled)return;
    const scope={...this.data.scope,[field.key]:field.options[index].value};
    if(field.key!=='grade')scope.grade='';
    if(scope.provider_kind && scope.provider_kind!=='GUARD')scope.security_level='ANY';
    if(scope.provider_kind==='GUARD' && scope.security_level==='ANY')scope.security_level='';
    this.setData({scope});this.updateScopeFields();this.stopVoice();this.load();
  },
  qualificationDetail(event) { const item=this.data.escorts.find(i=>i.id===Number(event.currentTarget.dataset.id));if(item)wx.showModal({title:'接单资格',content:(item.qualifications || []).filter(q=>q.is_active).map(q=>q.label).join('\n') || '资格待配置',showCancel:false}); },
  chooseGender(event) { if (this.data.fromCheckout) return; const genderIndex = Number(event.detail.value); if (![0, 1, 2].includes(genderIndex)) return; this.setData({ genderIndex }); this.stopVoice(); this.load(); },
  chooseSort(event) { const sort = Number(event.detail.value); if (![0, 1, 2].includes(sort)) return; this.setData({ sort }); this.filter(); },
  toggleVoice() { this.setData({ voiceOnly: !this.data.voiceOnly }); this.filter(); },
  resetFilters() { this.setData({ genderIndex: this.data.fromCheckout ? this.data.genderIndex : 0, voiceOnly: false, sort: 0, keyword: '', category:'', scope:{provider_kind:'',platform:'',security_level:'',grade:''} }); this.updateScopeFields();this.load(); },
  search(event) { this.setData({ keyword: event.detail.value }); this.filter(); },
  chooseCategory(event) { const id = event.currentTarget.dataset.id || ''; this.setData({ category: this.data.category === id ? '' : id }); this.stopVoice(); this.load(); },
  stopVoice() { if (this._audio) this._audio.stop(); if (this._alive) this.setData({ playingId: 0 }); },
  voice(event) {
    const provider = this.data.escorts.find(item => item.id === Number(event.currentTarget.dataset.id));
    if (!provider || !provider.voiceUrl) return;
    if (this.data.playingId === provider.id) { this.stopVoice(); return; }
    if (!this._audio) {
      this._audio = wx.createInnerAudioContext();
      this._audio.onEnded(() => { if (this._alive) this.setData({ playingId: 0 }); });
      this._audio.onError(() => { if (this._alive) { this.setData({ playingId: 0 }); toast('语音暂时无法播放，请稍后重试'); } });
    }
    this._audio.stop(); this._audio.src = provider.voiceUrl; this._audio.play(); this.setData({ playingId: provider.id });
  },
  openDetail(event) {
    if (this.data.loading || this.data.error || this.data.validating || this._openingDetail) return;
    if (!auth.ensure()) return;
    const provider = this.data.escorts.find(item => item.id === Number(event.currentTarget.dataset.id));
    if (!provider) return;
    this._openingDetail = true;
    this.stopVoice();
    const revision = auth.revision();
    const draft = this.data.isPicker && this.data.serviceId;
    const context = (this.data.serviceId ? '&service_id=' + this.data.serviceId : '')
      + '&provider_gender=' + ['ANY', 'MALE', 'FEMALE'][this.data.genderIndex]
      + (this.data.fromCheckout ? '&from_checkout=1' : '')
      + (draft ? '&picker_draft=1&selected_provider_ids=' + this.data.selectedProviderIds.join(',') : '');
    wx.navigateTo({ url: '/pages/companion-detail/index?id=' + provider.id + context,
      events: { providerSelected: selected => {
        if (!this._alive || revision !== auth.revision()) return;
        if (draft) {
          if (selected && !this.data.selectedProviderIds.includes(Number(selected.id))) this.applyProvider(selected);
        } else if (this.data.fromCheckout) this.getOpenerEventChannel().emit('providerSelected', selected);
      } }, fail: () => { this._openingDetail = false; toast('陪玩详情打开失败，请稍后重试'); } });
  },
  platform() {
    if (this.data.isPicker && this.data.serviceId) { if (!this.selectionAllowed()) return; this.updateSelection([]); return this.confirmSelection(); }
    if (this.data.fromCheckout) { if (this._openerRevision !== auth.revision()) return; this.getOpenerEventChannel().emit('providerSelected', null); wx.navigateBack(); }
    else if (this.data.serviceId) go('/pages/checkout/index?service_id=' + this.data.serviceId + '&provider_gender=' + ['ANY', 'MALE', 'FEMALE'][this.data.genderIndex]);
  },
  gift(event) { if (!auth.ensure()) return; this.stopVoice(); go('/pages/gifts/index?provider_id=' + Number(event.currentTarget.dataset.id)); },
  login() { auth.ensure(); }
});
