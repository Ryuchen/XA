const { privatePage } = require('../../core/page');
const { request } = require('../../core/api');
const { go, money, image } = require('../../core/ui');
const { brandAssets } = require('../../core/brand-assets');
Page(privatePage({
  data: { ...brandAssets(), profile: null, balance: '', stats: null, member: null },
  async fetch() {
    const [profile, wallet, stats, member] = await Promise.all([request('/users/me/'), request('/wallet/info/'), request('/orders/stats/'), request('/club/me/')]);
    return { profile: { ...profile, avatar: image(profile.avatar) }, balance: money(wallet.balance), stats, member };
  },
  open(event) { go('/pages/' + event.currentTarget.dataset.page + '/index'); },
  orders(event) { go('/pages/orders/index?status=' + (event.currentTarget.dataset.status || '')); },
  rights() { go('/pages/club-content/index?kind=RIGHTS'); },
}));
