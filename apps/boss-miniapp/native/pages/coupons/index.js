const api = require('../../core/api');
const auth = require('../../core/auth');
const ui = require('../../core/ui');
const { privatePage } = require('../../core/page');
Page(privatePage({
  data: { loading: false, error: '', items: [], tab: 'available', status: 'UNUSED', claiming: 0, using: 0,
    statuses: [{ value: 'UNUSED', label: '待使用' }, { value: 'USED', label: '已使用' }, { value: 'EXPIRED', label: '已过期' }, { value: 'DISABLED', label: '已停用' }] },
  init(options) { if (options.tab === 'mine') this.setData({ tab: 'mine' }); },
  async fetch() {
      const seq = this._seq;
      const items = ui.list(await api.request(this.data.tab === 'mine' ? '/coupons/mine/' : '/coupons/'));
      if (!this._alive || seq !== this._seq) return {};
      this._items = items.map(item => {
        const expired = item.status === 'UNUSED' && item.valid_to && new Date(item.valid_to).getTime() <= Date.now();
        return Object.assign({}, item, { status: item.availability_status || (expired ? 'EXPIRED' : item.status),
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
    if (!item || item.is_claimed || item.claim_status === 'USED' || !item.claimable || this.data.claiming || !auth.ensure()) return;
    const revision = auth.revision();
    this.setData({ claiming: id });
    try {
      await api.request('/coupons/' + id + '/claim/', 'POST');
      if (this._alive && this._visible && revision === auth.revision()) ui.toast('领取成功，可在我的券中查看');
    } catch (error) {
      if (this._alive && this._visible && revision === auth.revision()) {
        ui.toast(error.status === 0 ? '领取结果未确认，请查看我的券后再操作' : error);
      }
    } finally {
      if (this._alive && revision === auth.revision()) {
        if (this._visible) await this.load();
        if (this._alive && revision === auth.revision()) this.setData({ claiming: 0 });
      }
    }
  },
  async use(event) {
    const id = Number(event.currentTarget.dataset.id);
    const selected = this.data.items.find(row => row.id === id);
    if (!selected || selected.status !== 'UNUSED' || this.data.using || !auth.ensure()) return;
    const revision = auth.revision();
    this.setData({ using: id });
    try {
      const rows = ui.list(await api.request('/coupons/mine/?usable=1'));
      if (!this._alive || !this._visible || auth.revision() !== revision || this.data.tab !== 'mine') return;
      const coupon = rows.find(row => row.id === id);
      if (!coupon) { ui.toast('优惠券状态已变化，请重新查看'); await this.load(); return; }
      if (Array.isArray(coupon.service_ids) && !coupon.service_ids.length) return ui.toast('适用套餐待配置，请联系客服');
      wx.setStorageSync('xa:native:catalog-intent', { service_ids: coupon.service_ids, coupon_name: coupon.name, coupon_id: coupon.id });
      ui.go('/pages/catalog/index');
    } catch (error) {
      if (this._alive && this._visible && auth.revision() === revision) ui.toast(error);
    } finally { if (this._alive && auth.revision() === revision) this.setData({ using: 0 }); }
  }
}));
