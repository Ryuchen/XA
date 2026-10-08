const api = require('../../core/api');
const auth = require('../../core/auth');
const ui = require('../../core/ui');
const { privatePage } = require('../../core/page');
Page(privatePage({
  data: { loading: false, error: '', items: [] },
  async fetch() {
      const items = ui.list(await api.request('/announcements/?limit=20'));
      return { items: items.map(item => Object.assign({}, item, { dateText: ui.date(item.created_at) })) };
  },
  open(e) { ui.go('/pages/announcement-detail/index?id=' + e.currentTarget.dataset.id); }
}));
