const api = require('../../core/api');
const auth = require('../../core/auth');
const ui = require('../../core/ui');
const { privatePage } = require('../../core/page');
Page(privatePage({
  data: { loading: false, error: '', items: [], tab: 'available', status: 'UNUSED', claiming: 0,
    statuses: [{ value: 'UNUSED', label: '待使用' }, { value: 'USED', label: '已使用' }, { value: 'EXPIRED', label: '已过期' }] },
  init(options) { if (options.tab === 'mine') this.setData({ tab: 'mine' }); },
  async fetch() {
      const seq = this._seq;
      const items = ui.list(await api.request(this.data.tab === 'mine' ? '/coupons/mine/' : '/coupons/'));
      if (!this._alive || seq !== this._seq) return {};
      this._items = items.map(item => {
        const expired = item.status === 'UNUSED' && item.valid_to && new Date(item.valid_to).getTime() <= Date.now();
        return Object.assign({}, item, { status: expired ? 'EXPIRED' : item.status,
          amountText: item.discount_type === 'QUALIFICATION' ? '套餐' : item.discount_type === 'RATE' ? (item.discount_rate === 0 ? '赠送' : String(item.discount_rate / 10)) : ui.money(item.amount),
          unitText: item.discount_type === 'QUALIFICATION' ? '资格券' : item.discount_type === 'RATE' ? (item.discount_rate === 0 ? '服务券' : '折') : '兴安币',
          thresholdText: Array.isArray(item.service_ids) ? (item.service_ids.length ? '限指定套餐 · ' + item.service_ids.length + ' 款' + (item.limit_units ? ' · 限 ' + item.limit_units + ' 份' : '') : '适用套餐待配置，请联系客服') : item.discount_type === 'RATE' ? ({ALL:'与 VIP 折扣取优，不叠加',COMPANION:'指定陪玩服务',SAFE_BOX:'33 安全箱服务',EXPERIENCE:'68 元体验单'}[item.scope] || '') + (item.scope_variant ? ' · ' + item.scope_variant : '') + (item.limit_units ? ' · 限 ' + item.limit_units + ' 份' : '') : item.discount_type === 'DIRECT' || !item.threshold ? '无门槛' : '满 ' + ui.money(item.threshold) + ' 兴安币可用',
          dateText: item.valid_to ? ui.date(item.valid_to) : (item.experience_kind === 'DAILY_VALUE' ? '领取当日有效' : '永久有效'), capText: item.max_discount ? '每张最高优惠 ¥' + item.max_discount / 100 : '', platformText: item.platform === 'PC' ? '端游' : item.platform === 'MOBILE' ? '手游' : '' });
      });
      return { items: this._items.filter(item => this.data.tab !== 'mine' || item.status === this.data.status) };
  },
  renderItems() { this.setData({ items: (this._items || []).filter(item => this.data.tab !== 'mine' || item.status === this.data.status) }); },
  tab(e) {
    if (this.data.tab === e.currentTarget.dataset.tab) return;
    this.setData({ tab: e.currentTarget.dataset.tab, items: [] }); this._items = []; this.load();
  },
  filter(e) { this.setData({ status: e.currentTarget.dataset.status }); this.renderItems(); },
  async claim(e) {
    const id = Number(e.currentTarget.dataset.id);
    const item = this.data.items.find(coupon => coupon.id === id);
    if (!item || item.is_claimed || !item.claimable || this.data.claiming || !auth.ensure()) return;
    this.setData({ claiming: id });
    try { await api.request('/coupons/' + id + '/claim/', 'POST'); ui.toast('领取成功，可在我的券中查看'); }
    catch (error) { ui.toast(error); }
    finally { await this.load(); this.setData({ claiming: 0 }); }
  },
  use(event) {
    const coupon = this.data.items.find(row => row.id === Number(event.currentTarget.dataset.id));
    if (coupon && Array.isArray(coupon.service_ids)) {
      if (!coupon.service_ids.length) return ui.toast('适用套餐待配置，请联系客服');
      wx.setStorageSync('xa:native:catalog-intent', { service_ids: coupon.service_ids, coupon_name: coupon.name });
    } else wx.setStorageSync('xa:native:catalog-intent', {});
    ui.go('/pages/catalog/index');
  }
}));
