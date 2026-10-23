const { request } = require('../../core/api');
const auth = require('../../core/auth');
const { money, image, list, go, toast, date } = require('../../core/ui');
const { on } = require('../../core/events');
const { variantOptions } = require('../../core/catalog');
const requiresConsultation = service => service.is_addon || (service.purchase_mode === 'CONSULT' && service.billing_provider_count !== 2);

Page({
  data: { loading: false, refreshing: false, pendingVariantId: 0, variantError: '', error: '', logged: false, service: null, reviews: [], reviewError: '', reviewTotal: 0, reviewAverage: '', favoriteBusy: false, favoriteMotion: '', coverError: false, providerId: 0, variantPlatforms: [], variantLevels: [], variantChoices: [], variantLabels: [], variantIndex: 0, variantTotal: 0, addons: [] },
  onLoad(options) {
    this._alive = true;
    this._id = Number(options.id) || 0;
    this.setData({ providerId: Number(options.provider_id) || 0 });
    this._off = on('session.changed', () => { this._seq = (this._seq || 0) + 1; this._variants = []; this.setData({ service: null, loading: false, refreshing: false, pendingVariantId: 0, variantError: '', favoriteMotion: '', reviews: [], variantChoices: [], variantLabels: [], variantPlatforms: [], variantLevels: [], variantTotal: 0, addons: [] }); if (this._visible) this.load(); });
  },
  onShow() { this._visible = true; this.load(); },
  onHide() { this._visible = false; },
  onUnload() { this._alive = false; this._off(); },
  async onPullDownRefresh() { await this.load(); wx.stopPullDownRefresh(); },
  async load() {
    const seq = this._seq = (this._seq || 0) + 1;
    const logged = !!auth.getSession();
    // 已有详情始终留在视图中；切换规格只更新数据，不销毁页面和滚动位置。
    this.setData({ loading: logged && !this.data.service, refreshing: logged && !!this.data.service, logged, error: '', variantError: '', favoriteMotion: '' });
    if (!logged) return;
    if (!this._id) { this.setData({ loading: false, error: '缺少服务编号，请从服务目录重新进入' }); return; }
    const [detail, reviews, providers] = await Promise.allSettled([request('/orders/services/' + this._id + '/'), request('/orders/services/' + this._id + '/evaluations/'), this.data.providerId ? request('/users/escorts/') : Promise.resolve([])]);
    if (!this._alive || seq !== this._seq) return;
    const fail = message => this.setData({ loading: false, refreshing: false, pendingVariantId: 0,
      ...(this.data.service ? { variantError: message } : { error: message, service: null }) });
    if (detail.status === 'rejected') { fail(detail.reason.message); return; }
    const service = detail.value;
    let variants = list(service.variants).length ? list(service.variants) : [service];
    if (this.data.providerId) {
      const provider = providers.status === 'fulfilled' && list(providers.value).find(item => item.id === this.data.providerId);
      const allowed = provider && list(provider.service_item_ids);
      if (!allowed || !allowed.includes(service.id)) { fail('该陪玩暂不支持此套餐，请返回目录重新选择'); return; }
      variants = variants.filter(item => allowed.includes(item.id));
    }
    this._variants = variants;
    const { variants: ignored, ...visibleService } = service;
    const cover = image(service.cover_url || service.gameplay_cover_url);
    const detailsImages = list(service.details_images).map(image).filter(Boolean);
    const gallery = list(service.images).map(image).filter(url => url && url !== cover);
    const selectedSummary = [service.platform_name, service.security_level && service.security_level !== 'ANY' ? service.security_level_name : '', service.catalog_variant || service.name].filter(Boolean).join(' · ');
    this.setData({ ...variantOptions(variants, service), loading: false, refreshing: false, pendingVariantId: 0, variantError: '', coverError: false, service: { ...visibleService, cover, selectedSummary, requiresConsultation: requiresConsultation(service), isDoubleHour: service.service_type === 'COMPANION' && service.billing_provider_count === 2 && ['小时', '时', 'hour'].includes(service.price_unit), familyName: !service.is_gift && service.catalog_family || service.name, displayPrice: money(service.price), detailsHtml: (service.details_html || "").replace(/\sdata-original="[^"]*"/g, "").replace(/src="([^"]+)"/g, (_, url) => 'src="' + image(url) + '"'), detailsImages, gallery: Array.from(new Set(gallery)), introductionImages: list(service.introduction_images).map(image).filter(Boolean), rulesImages: list(service.rules_images).map(image).filter(url => url && !detailsImages.includes(url)), pool: list(service.roulette_pool), highlights: list(service.highlights) }, reviews: reviews.status === 'fulfilled' ? list(reviews.value.list).map(item => ({ ...item, avatar: image(item.customer_avatar), created: date(item.created_at) })) : [], reviewTotal: reviews.status === 'fulfilled' ? reviews.value.total || 0 : 0, reviewAverage: reviews.status === 'fulfilled' && reviews.value.total ? Number(reviews.value.avg_score).toFixed(1) : '', reviewError: reviews.status === 'rejected' ? '评价暂未加载，点击重试' : '' });
  },
  coverFailed() { this.setData({ coverError: true }); },
  chooseSpec(event) {
    const { field, id } = event.currentTarget.dataset, current = this.data.service;
    if (!current || !['platform', 'security_level'].includes(field)) return;
    let choices = (this._variants || []).filter(item => !item.is_addon && !item.is_gift && item[field] === id);
    if (field === 'security_level') choices = choices.filter(item => item.platform === current.platform);
    else if (choices.some(item => item.security_level === current.security_level)) choices = choices.filter(item => item.security_level === current.security_level);
    const next = choices.find(item => item.catalog_variant === current.catalog_variant) || choices[0];
    if (next) return this.selectVariant(next.id);
  },
  chooseVariant(event) { return this.selectVariant(Number(event.currentTarget.dataset.id)); },
  pickVariant(event) { const item = this.data.variantChoices[Number(event.detail.value)]; if (item) return this.selectVariant(item.id); },
  selectVariant(id) {
    if (!(this._variants || []).some(item => item.id === id) || (id === this._id && !this.data.variantError)) return;
    this._id = id;
    this.setData({ pendingVariantId: id });
    return this.load();
  },
  async favorite() {
    if (!auth.ensure() || this.data.favoriteBusy || this.data.refreshing || this.data.variantError || !this.data.service) return;
    const id = this._id, wasFavorited = !!this.data.service.is_favorited;
    this.setData({ favoriteBusy: true, favoriteMotion: '' });
    try {
      const result = await request('/orders/favorites/toggle/', 'POST', { service_id: id });
      if (this._alive && this._id === id && this.data.service && this.data.service.id === id) {
        const favorited = !!result.favorited;
        this.setData({ 'service.is_favorited': favorited, favoriteMotion: favorited === wasFavorited ? '' : (favorited ? 'added' : 'removed') });
      }
    }
    catch (error) { if (this._alive) toast(error); }
    finally { if (this._alive) this.setData({ favoriteBusy: false }); }
  },
  checkout() { if (!this.data.service || this.data.loading || this.data.refreshing || this.data.variantError || this.data.error) return; if (requiresConsultation(this.data.service)) { this.support(); return; } if (auth.ensure()) go('/pages/checkout/index?service_id=' + this.data.service.id + (this.data.providerId ? '&provider_id=' + this.data.providerId : '')); },
  companions() { if (!this.data.service || this.data.loading || this.data.refreshing || this.data.variantError || this.data.error) return; if (requiresConsultation(this.data.service)) { this.support(); return; } if (auth.ensure()) go('/pages/companion-picker/index?service_id=' + this.data.service.id); },
  orders() { go('/pages/orders/index'); },
  support() { go('/pages/support/index'); },
  login() { auth.ensure(); },
  onShareAppMessage() { return { title: this.data.service ? this.data.service.name : '兴安电竞服务', path: '/pages/service/index?id=' + (this.data.service ? this.data.service.id : this._id) }; }
});
