const { request } = require('./api');
const auth = require('./auth');
const { image, list, go, toast } = require('./ui');
const { prepareCompanion } = require('./companion');
const { on } = require('./events');

module.exports = (options = {}) => ({
  data: { loading: false, error: '', logged: false, escorts: [], visible: [], categories: [], category: '', keyword: '', serviceId: 0, serviceName: '', fromCheckout: false, playingId: 0,
    isPicker: !!options.picker,
    qualificationMeta: {kinds:[],platforms:[],scopes:[]}, scope: {provider_kind:'',platform:'',security_level:'',grade:''}, scopeFields: [], serviceRequirement: '',
    voiceOnly: false, genderOptions: ['不限', '男陪', '女陪'], genderIndex: 0, sort: 0, sortLabels: ['默认排序','时价低到高','时价高到低'], sortOptions: ['默认排序', '参考时价从低到高', '参考时价从高到低'] },
  onLoad(options) {
    this._alive = true;
    this.setData({ serviceId: Number(options.service_id) || 0, fromCheckout: options.from_checkout === '1', genderIndex: Math.max(0, ['ANY', 'MALE', 'FEMALE'].indexOf(options.provider_gender || 'ANY')) });
    this._off = on('session.changed', () => { this.stopVoice(); this._seq = (this._seq || 0) + 1; this.setData({ escorts: [], visible: [] }); if (this._visible) this.load(); });
  },
  onShow() { this._visible = true; this._openingDetail = false; this.load(); },
  onHide() { this._visible = false; this.stopVoice(); },
  onUnload() { this._alive = false; this._off(); if (this._audio) this._audio.destroy(); },
  async onPullDownRefresh() { await this.load(); wx.stopPullDownRefresh(); },
  async load() {
    const seq = this._seq = (this._seq || 0) + 1;
    const logged = !!auth.getSession();
    this.setData({ logged, loading: logged, error: '' });
    if (!logged) { this.setData({ escorts: [], visible: [] }); return; }
    const { serviceId, category } = this.data;
    const servicePicker = this.data.isPicker && serviceId;
    const scopeQuery = serviceId ? '' : Object.entries(this.data.scope).filter(([,v]) => v).map(([k,v]) => '&'+k+'='+encodeURIComponent(v)).join('');
    const query = serviceId ? '?service_item=' + serviceId + (this.data.isPicker ? '&picker=1' : '') : '?directory=1' + (category ? '&game_category=' + category : '');
    try {
      const [escorts, categories, service, qualificationMeta] = await Promise.all([request('/users/escorts/' + query + scopeQuery + (this.data.genderIndex ? '&gender=' + ['ANY', 'MALE', 'FEMALE'][this.data.genderIndex] : '')), servicePicker ? Promise.resolve([]) : request('/orders/game-categories/'), serviceId ? request('/orders/services/' + serviceId + '/') : Promise.resolve(null), servicePicker ? Promise.resolve(null) : request('/users/qualification-options/')]);
      if (!this._alive || seq !== this._seq) return;
      this.setData({ loading: false, escorts: list(escorts).map(prepareCompanion), categories: list(categories).map(item => ({ ...item, id: String(item.id), iconUrl: image(item.icon_url) || '/assets/icons/headphones.png' })), qualificationMeta: qualificationMeta || {kinds:[],platforms:[],scopes:[]}, serviceRequirement: service?.provider_requirement?.label || '', serviceName: service ? service.name : '' });
      this.updateScopeFields(); this.filter();
    } catch (error) { if (this._alive && seq === this._seq) { this.stopVoice(); this.setData({ loading: false, error: error.message, escorts: [], visible: [] }); } }
  },
  filter() {
    const { voiceOnly, sort, genderIndex } = this.data;
    const query = this.data.keyword.trim().toLowerCase();
    const visible = this.data.escorts.filter(item => {
      if (query && ![item.nickname, item.rank, item.bio, item.city].join(' ').toLowerCase().includes(query)) return false;
      if (genderIndex && item.gender !== ['ANY', 'MALE', 'FEMALE'][genderIndex]) return false;
      if (voiceOnly && !item.voiceUrl) return false;
      return true;
    });
    if (sort) visible.sort((a, b) => {
      if (a.hasHourlyPrice !== b.hasHourlyPrice) return a.hasHourlyPrice ? -1 : 1;
      return sort === 1 ? Number(a.pricePerHour) - Number(b.pricePerHour) : Number(b.pricePerHour) - Number(a.pricePerHour);
    });
    if (this.data.playingId && !visible.some(item => item.id === this.data.playingId)) this.stopVoice();
    this.setData({ visible });
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
    if (this.data.loading || this.data.error || this._openingDetail) return;
    if (!auth.ensure()) return;
    const provider = this.data.escorts.find(item => item.id === Number(event.currentTarget.dataset.id));
    if (!provider) return;
    this._openingDetail = true;
    this.stopVoice();
    const revision = auth.revision();
    const context = (this.data.serviceId ? '&service_id=' + this.data.serviceId : '')
      + '&provider_gender=' + ['ANY', 'MALE', 'FEMALE'][this.data.genderIndex]
      + (this.data.fromCheckout ? '&from_checkout=1' : '');
    wx.navigateTo({ url: '/pages/companion-detail/index?id=' + provider.id + context,
      events: { providerSelected: selected => {
        if (this._alive && revision === auth.revision() && this.data.fromCheckout) this.getOpenerEventChannel().emit('providerSelected', selected);
      } }, fail: () => { this._openingDetail = false; toast('陪玩详情打开失败，请稍后重试'); } });
  },
  platform() { if (this.data.fromCheckout) { this.getOpenerEventChannel().emit('providerSelected', null); wx.navigateBack(); } else if (this.data.serviceId) go('/pages/checkout/index?service_id=' + this.data.serviceId + '&provider_gender=' + ['ANY', 'MALE', 'FEMALE'][this.data.genderIndex]); },
  gift(event) { if (!auth.ensure()) return; this.stopVoice(); go('/pages/gifts/index?provider_id=' + Number(event.currentTarget.dataset.id)); },
  login() { auth.ensure(); }
});
