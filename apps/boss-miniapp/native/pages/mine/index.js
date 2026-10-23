const { privatePage } = require('../../core/page');
const { request } = require('../../core/api');
const { go, money, image } = require('../../core/ui');
const { brandAssets } = require('../../core/brand-assets');
const auth = require('../../core/auth');
const { on } = require('../../core/events');
const page = privatePage({
  data: { ...brandAssets(), profile: null, balance: '', stats: null, member: null },
  init() {
    const refresh = payload => {
      const session = auth.getSession();
      if (!this._alive || !this._visible || !session || (payload?.account_id != null && String(payload.account_id) !== String(session.user.id))) return;
      ++this._seq;
      const revision = auth.revision();
      if (!this._refreshTimer) this._refreshTimer = setTimeout(() => {
        this._refreshTimer = null;
        if (this._alive && this._visible && revision === auth.revision()) this.load();
      }, 300);
    };
    this._mineOff = [on('order.changed', refresh), on('wallet.changed', refresh), on('session.changed', () => { clearTimeout(this._refreshTimer); this._refreshTimer = null; })];
  },
  async fetch() {
    const [profile, wallet, stats, member] = await Promise.all([request('/users/me/'), request('/wallet/info/'), request('/orders/stats/'), request('/club/me/')]);
    return { profile: { ...profile, avatar: image(profile.avatar) }, balance: money(wallet.balance), stats, member };
  },
  open(event) { go('/pages/' + event.currentTarget.dataset.page + '/index'); },
  orders(event) { go('/pages/orders/index?status=' + (event.currentTarget.dataset.status || '')); },
  rights() { go('/pages/club-content/index?kind=RIGHTS'); },
});
const hide = page.onHide, unload = page.onUnload;
Page({ ...page,
  onHide() { hide.call(this); ++this._seq; clearTimeout(this._refreshTimer); this._refreshTimer = null; },
  onUnload() { this._mineOff.forEach(off => off()); clearTimeout(this._refreshTimer); this._refreshTimer = null; unload.call(this); },
});
