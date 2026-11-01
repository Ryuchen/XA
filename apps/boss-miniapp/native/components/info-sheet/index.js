Component({
  properties: { label: { type: String, value: '查看说明' }, title: { type: String, value: '说明' } },
  data: { visible: false, open: false },
  lifetimes: { attached() { this._alive = true; this._revision = 0; }, detached() { this._alive = false; this._revision++; clearTimeout(this._timer); } },
  pageLifetimes: { hide() { this.closeNow(); } },
  methods: {
    noop() {},
    closeNow() {
      this._revision++;
      clearTimeout(this._timer);
      const wasVisible = this.data.visible;
      this.setData({ visible: false, open: false });
      if (wasVisible) this.triggerEvent('visibilitychange', { open: false });
    },
    show() {
      if (this.data.open) return;
      const revision = ++this._revision;
      clearTimeout(this._timer);
      this.setData({ visible: true }, () => wx.nextTick(() => { if (this._alive && revision === this._revision) this.setData({ open: true }); }));
      this.triggerEvent('visibilitychange', { open: true });
    },
    hide() {
      const revision = ++this._revision;
      this.setData({ open: false });
      clearTimeout(this._timer);
      this._timer = setTimeout(() => { if (this._alive && revision === this._revision) { this.setData({ visible: false }); this.triggerEvent('visibilitychange', { open: false }); } }, 350);
    },
  },
});
