const { request } = require('../../core/api');
const { image, go, richText, date, toast } = require('../../core/ui');

// Keep authored HTML while fitting artwork to the content's reading area.
function responsiveContent(value) {
  const imageStyle = 'max-width:100% !important;height:auto !important;';
  return richText(value).replace(/<img\b(?:[^"'<>]|"[^"]*"|'[^']*')*>/gi, tag => {
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

Page({
  data: { loading: false, loadingMore: false, error: '', items: [], item: null, title: '俱乐部规范', activityList: false, imageOnly: false, more: false },
  onLoad(options) { this._alive = true; this._id = Number(options.id) || 0; this._kind = options.kind || 'RULES'; this._seq = 0; this.load(); },
  onUnload() { this._alive = false; this._seq++; },
  async onPullDownRefresh() { await this.load(); wx.stopPullDownRefresh(); },
  onReachBottom() { if (this.data.more) this.more(); },
  present(row) { return { ...row, image: image(row.image_url), content: responsiveContent(row.content), date: date(row.starts_at || row.created_at).slice(0, 10) }; },
  async load() {
    const seq = ++this._seq, activityList = !this._id && this._kind === 'HOT';
    this.setData({ loading: true, loadingMore: false, error: '', activityList });
    try {
      const data = await request('/club/content/' + (this._id ? this._id + '/' : activityList ? '?kind=HOT&archive=1&page=1' : ''));
      if (!this._alive || seq !== this._seq) return;
      if (this._id) {
        this.setData({ item: this.present(data), imageOnly: data.kind === 'HOT', loading: false });
        wx.setNavigationBarTitle({ title: data.title });
      } else {
        const kinds = this._kind === 'RULES' ? ['ORDER_RULES', 'PROVIDER_RULES', 'AFTERSALE_RULES'] : [this._kind];
        const title = activityList ? '俱乐部活动' : this._kind === 'RIGHTS' ? '老板权益' : this._kind === 'POINT_RULES' ? '积分与兑换规则' : '俱乐部规范';
        this._page = 1;
        this.setData({ items: data.filter(row => kinds.includes(row.kind)).map(row => this.present(row)), loading: false, title, more: activityList && data.length === 30 });
        wx.setNavigationBarTitle({ title });
      }
    } catch (e) { if (this._alive && seq === this._seq) this.setData({ loading: false, error: e.message }); }
  },
  async more() {
    if (!this.data.more || this.data.loadingMore || this.data.loading) return;
    const seq = this._seq;
    this.setData({ loadingMore: true });
    try {
      const rows = await request('/club/content/?kind=HOT&archive=1&page=' + (this._page + 1));
      if (!this._alive || seq !== this._seq) return;
      this._page++;
      const ids = new Set(this.data.items.map(row => row.id));
      this.setData({ items: [...this.data.items, ...rows.filter(row => !ids.has(row.id)).map(row => this.present(row))], more: rows.length === 30 });
    } catch (e) { if (this._alive && seq === this._seq) toast(e); }
    finally { if (this._alive && seq === this._seq) this.setData({ loadingMore: false }); }
  },
  open(event) { go('/pages/club-content/index?id=' + event.currentTarget.dataset.id); },
  preview(event) { const url = event.currentTarget.dataset.url; if (url) wx.previewImage({ urls: [url], current: url }); }
});
