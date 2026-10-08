const api = require('../../core/api');
const auth = require('../../core/auth');
const ui = require('../../core/ui');
const { privatePage } = require('../../core/page');
Page(privatePage({
  data: { loading: false, error: '', item: null },
  init(options) { this._id = options.id; },
  async fetch() {
    if (!/^[1-9]\d*$/.test(this._id || '')) throw new Error('公告链接无效');
      const item = await api.request('/announcements/' + this._id + '/');
      return { item: Object.assign({}, item, { dateText: ui.date(item.created_at), content: ui.richText(item.content) }) };
  }
}));
