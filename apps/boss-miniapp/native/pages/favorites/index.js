const api = require('../../core/api');
const auth = require('../../core/auth');
const ui = require('../../core/ui');
const { privatePage } = require('../../core/page');
Page(privatePage({
  data: { loading: false, error: '', items: [], removing: 0 },
  async fetch() {
      const items = ui.list(await api.request('/orders/favorites/'));
      return { items: items.map(item => Object.assign({}, item, { priceText: ui.money(item.price), cover: ui.image(item.cover_url) })) };
  },
  open(e) { if (e.currentTarget.dataset.available === false) { ui.toast('服务暂不可购买'); return; } ui.go('/pages/service/index?id=' + e.currentTarget.dataset.id); },
  browse() { ui.go('/pages/catalog/index'); },
  async remove(e) {
    const id = Number(e.currentTarget.dataset.id);
    if (!id || this.data.removing || !auth.ensure()) return;
    this.setData({ removing: id });
    try {
      await api.request('/orders/favorites/' + id + '/', 'DELETE');
      this.setData({ items: this.data.items.filter(item => item.id !== id) });
    } catch (error) {
      ui.toast(error); await this.load();
    } finally { this.setData({ removing: 0 }); }
  }
}));
