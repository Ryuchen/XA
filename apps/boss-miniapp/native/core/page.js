const auth = require('./auth');
const { on } = require('./events');
// Shared lifecycle for private read screens. Responses cannot cross account or page lifetimes.
function privatePage(definition) {
  return {
    ...definition,
    data: { logged: false, loading: false, error: '', ...definition.data },
    onLoad(options) {
      this._alive = true;
      const nativeSetData = this.setData.bind(this);
      this.setData = (patch, callback) => { if (this._alive) nativeSetData(patch, callback); };
      this._off = on('session.changed', () => {
        this._seq = (this._seq || 0) + 1;
        this._items = [];
        this.setData({ ...definition.data, logged: !!auth.getSession(), error: '' });
        if (this._visible) this.load();
      });
      if (definition.init) definition.init.call(this, options);
    },
    onShow() { this._visible = true; this.load(); },
    onHide() { this._visible = false; },
    onUnload() { this._alive = false; this._off(); },
    async onPullDownRefresh() { await this.load(); wx.stopPullDownRefresh(); },
    async load() {
      const seq = this._seq = (this._seq || 0) + 1;
      const session = auth.getSession();
      this.setData({ logged: !!session, loading: !!session, error: '' });
      if (!session) return;
      try {
        const patch = await definition.fetch.call(this);
        if (this._alive && seq === this._seq) this.setData({ ...patch, loading: false });
      } catch (error) { if (this._alive && seq === this._seq) this.setData({ loading: false, error: error.message }); }
    },
    login() { auth.ensure(); },
  };
}
module.exports = { privatePage };
