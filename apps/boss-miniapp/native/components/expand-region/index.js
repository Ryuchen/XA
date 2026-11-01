Component({
  properties: { expanded: { type: Boolean, value: false, observer() { if (this._ready) this.resize(); } } },
  data: { height: '0px' },
  lifetimes: {
    ready() { this._ready = true; this._revision = 0; this.resize(); },
    detached() { this._ready = false; this._revision++; clearTimeout(this._timer); },
  },
  methods: {
    resize() {
      const revision = ++this._revision;
      clearTimeout(this._timer);
      const open = this.properties.expanded;
      this.createSelectorQuery().select('.expand-inner').boundingClientRect(rect => {
        if (!this._ready || revision !== this._revision || !rect) return;
        if (open) {
          this.setData({ height: rect.height + 'px' });
          this._timer = setTimeout(() => { if (this._ready && revision === this._revision && this.properties.expanded) this.setData({ height: 'auto' }); }, 350);
        }
        else {
          this.setData({ height: rect.height + 'px' }, () => {
            this._timer = setTimeout(() => { if (this._ready && revision === this._revision) this.setData({ height: '0px' }); }, 20);
          });
        }
      }).exec();
    },
  },
});
