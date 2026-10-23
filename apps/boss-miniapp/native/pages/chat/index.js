const auth = require('../../core/auth');
const { request, upload } = require('../../core/api');
const { on, emit } = require('../../core/events');
const { image, date, toast } = require('../../core/ui');
Page({
  data: { logged: false, loading: false, error: '', items: [], content: '', sending: false, scrollTo: '' },
  onLoad(options) {
    this._alive = true;
    if (/^[1-9]\d*$/.test(options.order_id || '')) this.setData({ content: '我想咨询订单编号 ' + options.order_id + ' 的服务情况。' });
    this._off = on('message.changed', () => { if (this._visible) this.load(true); });
    this._sessionOff = on('session.changed', () => { this._seq++; this.setData({ items: [], content: '', logged: !!auth.getSession() }); if (this._visible) this.load(); });
  },
  onShow() { this._visible = true; this.load(); },
  onHide() { this._visible = false; },
  onUnload() { this._alive = false; this._off(); this._sessionOff(); },
  onPullDownRefresh() { return this.load().finally(() => wx.stopPullDownRefresh()); },
  async load(quiet = false) {
    const seq = this._seq = (this._seq || 0) + 1, logged = !!auth.getSession();
    this.setData({ logged, loading: logged && !quiet, error: '' });
    if (!logged) return;
    try {
      const result = await request('/chat/session/');
      if (!this._alive || seq !== this._seq) return;
      const items = result.messages.map(item => ({ ...item, image: image(item.image_url), time: date(item.created_at) }));
      this.setData({ items, scrollTo: items.length ? 'message-' + items[items.length - 1].id : '' });
      if (this._visible) await request('/chat/read/', 'POST');
    } catch (error) { if (this._alive && seq === this._seq) this.setData({ error: error.message }); }
    finally { if (this._alive && seq === this._seq) this.setData({ loading: false }); }
  },
  input(event) { this.setData({ content: event.detail.value }); },
  async send() {
    if (!auth.ensure() || this.data.sending || !this.data.content.trim()) return;
    const content = this.data.content.trim();
    this.setData({ sending: true });
    try {
      await request('/chat/send/', 'POST', { content });
      if (this._alive) { if (this.data.content.trim() === content) this.setData({ content: '' }); await this.load(true); emit('message.changed'); }
    } catch (error) { if (this._alive) toast(error); }
    finally { if (this._alive) this.setData({ sending: false }); }
  },
  chooseImage() {
    if (!auth.ensure() || this.data.sending) return;
    wx.chooseMedia({ count: 1, mediaType: ['image'], sizeType: ['compressed'], sourceType: ['album', 'camera'], success: result => this.sendImage(result.tempFiles[0]) });
  },
  async sendImage(file) {
    if (!file || this.data.sending || !this._alive) return;
    if (file.size > 5 * 1024 * 1024) { toast('图片大小不能超过 5MB'); return; }
    this.setData({ sending: true });
    try { await upload('/chat/send/', file.tempFilePath); if (this._alive) { await this.load(true); emit('message.changed'); } }
    catch (error) { if (this._alive) toast(error); }
    finally { if (this._alive) this.setData({ sending: false }); }
  },
  async preview(event) {
    let url = event.currentTarget.dataset.url;
    if (!url || this._previewing) return;
    this._previewing = true;
    try {
      const ticket = url.match(/\/api\/media\/private\/([^/?]+)\/?$/);
      if (ticket) url = image((await request('/media/renew/', 'POST', { ticket: ticket[1] })).url);
      if (this._alive && this._visible) wx.previewImage({ urls: [url] });
    } catch (error) { if (this._alive) toast(error); }
    finally { this._previewing = false; }
  },
  async imageError(event) {
    const id = Number(event.currentTarget.dataset.id);
    const item = this.data.items.find(item => item.id === id);
    if (!item || item._renewed) return;
    const ticket = item.image.match(/\/api\/media\/private\/([^/?]+)\/?$/);
    if (!ticket) return;
    const index = this.data.items.indexOf(item);
    this.setData({ ['items[' + index + ']._renewed']: true });
    try {
      const result = await request('/media/renew/', 'POST', { ticket: ticket[1] });
      if (!this._alive) return;
      const current = this.data.items.findIndex(value => value.id === id && value.image === item.image);
      if (current >= 0) this.setData({ ['items[' + current + '].image']: image(result.url) });
    } catch { /* A preview click retries with a visible error. */ }
  },
  login() { auth.ensure(); },
});
