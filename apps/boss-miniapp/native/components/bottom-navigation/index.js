const { tabs, selectedTab } = require('../../core/navigation');
Component({
  data: { tabs, selected: 'home' },
  lifetimes: {
    attached() { this.syncRoute(); },
  },
  pageLifetimes: {
    show() { this.syncRoute(); },
  },
  methods: {
    syncRoute() {
      const pages = getCurrentPages();
      this._route = pages.length ? pages[pages.length - 1].route : '';
      this.setData({ selected: selectedTab(this._route) });
    },
    openTab(event) {
      const key = event.currentTarget.dataset.key;
      if (!tabs.some(tab => tab.key === key) || this._switching) return;
      const url = '/pages/' + key + '/index';
      if ('/' + this._route === url) return;
      this._switching = true;
      try {
        wx.switchTab({
          url,
          fail() { wx.showToast({ title: '页面打开失败，请重试', icon: 'none' }); },
          complete: () => { this._switching = false; },
        });
      } catch (_) {
        this._switching = false;
        wx.showToast({ title: '页面打开失败，请重试', icon: 'none' });
      }
    },
  },
});
