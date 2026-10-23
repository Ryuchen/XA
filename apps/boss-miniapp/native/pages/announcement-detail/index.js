const api = require('../../core/api');
const auth = require('../../core/auth');
const ui = require('../../core/ui');
const { privatePage } = require('../../core/page');

// Keep authored HTML while fitting artwork to the announcement's reading area.
function responsiveContent(value) {
  const imageStyle = 'max-width:100% !important;height:auto !important;';
  return ui.richText(value).replace(/<img\b(?:[^"'<>]|"[^"]*"|'[^']*')*>/gi, tag => {
    let styled = false;
    const image = tag.replace(/(\s+)([^\s"'<>/=]+)(\s*=\s*)(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+))/g,
      (attribute, space, name, equals, double, single, bare) => {
        if (name.toLowerCase() !== 'style') return attribute;
        styled = true;
        const quote = single !== undefined ? "'" : '"';
        const style = double !== undefined ? double : single !== undefined ? single : bare;
        return space + name + equals + quote + style + ';' + imageStyle + quote;
      });
    return styled ? image : image.replace(/^<img\b/i, '<img style="' + imageStyle + '"');
  });
}

Page(privatePage({
  data: { loading: false, error: '', item: null },
  init(options) { this._id = options.id; },
  async fetch() {
    if (!/^[1-9]\d*$/.test(this._id || '')) throw new Error('公告链接无效');
      const item = await api.request('/announcements/' + this._id + '/');
      return { item: Object.assign({}, item, { dateText: ui.date(item.created_at), content: responsiveContent(item.content) }) };
  }
}));
