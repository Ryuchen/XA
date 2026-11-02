const { modal: themedModal } = require('../../core/ui');
const { request } = require('../../core/api');
const auth = require('../../core/auth');
const { money, image, list, go, toast } = require('../../core/ui');
const { on } = require('../../core/events');
const { brandAssets } = require('../../core/brand-assets');

Page({
  data: { ...brandAssets(), loading: true, error: '', logged: false, gameplays: [], banners: [], announcements: [], stats: null, privateError: '', feed: [], checkin: null, contents: [], hot: [], rights: [], popup: null, popupItems: [], popupIndex: 0, keyword: '', bannerIndex: 0 },
  onLoad() {
    this._alive = true;
    this._off = on('session.changed', () => { this._seq++; this.setData({ stats: null, banners: [], announcements: [], feed: [], checkin: null }); if (this._visible) this.load(); });
    this._offOrders = on('order.changed', () => { if (this._visible) this.load(); });
    this._offFeed = on('feed.changed', () => { if (this._visible) this.refreshFeed(); });
  },
  onShow() { this._visible = true; this.load(); this.startFeed(); },
  onHide() { this._visible = false; clearInterval(this._feedTimer); },
  onUnload() { this._alive = false; clearInterval(this._feedTimer); this._off(); this._offOrders(); this._offFeed(); },
  async onPullDownRefresh() { await this.load(); wx.stopPullDownRefresh(); },
  async load() {
    const seq = this._seq = (this._seq || 0) + 1;
    const logged = !!auth.getSession();
    this.setData({ loading: true, error: '', privateError: '', logged });
    const result = await Promise.allSettled([
      request('/orders/gameplays/'),
      logged ? request('/orders/stats/') : Promise.resolve(null),
      logged ? request('/banners/') : Promise.resolve([]),
      logged ? request('/announcements/?limit=8') : Promise.resolve([]),
      logged ? request('/club/feed/') : Promise.resolve([]),
      logged ? request('/users/checkin/') : Promise.resolve(null),
      request('/club/content/'), request('/club/content/?kind=HOT')
    ]);
    if (!this._alive || seq !== this._seq) return;
    const [plays, stats, banners, announcements, feed, checkin, contents, activities] = result;
    const contentRows = contents.status === 'fulfilled' ? contents.value.map(row => ({ ...row, image: image(row.image_url) })) : [];
    const hot = activities.status === 'fulfilled' ? activities.value.slice(0, 1).map(row => ({ ...row, image: image(row.image_url) })) : [];
    this.setData({
      loading: false,
      error: plays.status === 'rejected' ? plays.reason.message : '',
      gameplays: plays.status === 'fulfilled' ? list(plays.value).map(item => ({ ...item, cover: image(item.cover_url), starting: item.starting_price == null ? '' : money(item.starting_price) })) : [],
      stats: stats.status === 'fulfilled' ? stats.value : null,
      banners: banners.status === 'fulfilled' ? list(banners.value).map(item => ({ ...item, image: image(item.image_url) })).filter(item => item.image) : [],
      announcements: announcements.status === 'fulfilled' ? list(announcements.value) : [],
      feed: feed.status === 'fulfilled' ? feed.value : [],
      checkin: checkin.status === 'fulfilled' ? checkin.value : null,
      checkinDays: checkin.status === 'fulfilled' && checkin.value ? this.checkinPreview(checkin.value) : [],
      contents: contentRows, hot, rights: contentRows.filter(row => row.kind === 'RIGHTS'),
      privateError: logged && stats.status === 'rejected' ? '个人服务暂未更新，点击重试' : ''
    });
    const popupItems = [hot.find(row => row.image), contentRows.find(row => row.kind === 'ORDER_RULES' && row.image)].filter(Boolean);
    if (popupItems.length) {
      const key = 'xa:entry-posters:' + popupItems.map(row => row.id + ':' + row.updated_at).join('|');
      if (!wx.getStorageSync(key)) { this._popupKey = key; this.setData({ popup: popupItems[0], popupItems, popupIndex: 0 }); }
    }
  },
  startFeed() { clearInterval(this._feedTimer); this._feedTimer = setInterval(async () => { if (!this._visible || !auth.getSession()) return; const seq = this._seq; try { const feed = await request('/club/feed/'); if (this._alive && this._visible && seq === this._seq) this.setData({ feed }); } catch (_) { /* retain the last successful feed */ } }, 60000); },
  async refreshFeed() { if (!auth.getSession()) return; const seq = this._seq; try { const feed = await request('/club/feed/'); if (this._alive && seq === this._seq) this.setData({ feed }); } catch (_) {} },
  checkinPreview(calendar) { const start = Math.min(Math.max(1, calendar.today - 3), calendar.days_in_month - 6); return Array.from({ length: 7 }, (_, index) => { const day = start + index; return { day, today: day === calendar.today, checked: list(calendar.checked_days).includes(day) }; }); },
  gifts() { go('/pages/gifts/index'); },
  rights() { go('/pages/club-content/index?kind=RIGHTS'); },
  messages() { go('/pages/messages/index'); },
  closePopup() { if (this._popupKey) wx.setStorageSync(this._popupKey, true); this.setData({ popup: null }); },
  popupTab(event) { const index = Number(event.currentTarget.dataset.index); if (this.data.popupItems[index]) this.setData({ popup: this.data.popupItems[index], popupIndex: index }); },
  popupNext() { if (this.data.popupIndex < this.data.popupItems.length - 1) this.setData({ popupIndex: this.data.popupIndex + 1, popup: this.data.popupItems[this.data.popupIndex + 1] }); else this.closePopup(); },
  previewPopup() { const url = this.data.popup && this.data.popup.image; if (url) wx.previewImage({ urls: [url], current: url }); },
  activities() { go('/pages/club-content/index?kind=HOT'); },
  content(event) { go('/pages/club-content/index?id=' + event.currentTarget.dataset.id); },
  checkin() { go('/pages/checkin/index'); },
  points() { go('/pages/points/index'); },
  prevent() {},
  login() { auth.ensure(); },
  searchInput(event) { this.setData({ keyword: event.detail.value }); },
  search() { go('/pages/catalog/index?keyword=' + encodeURIComponent(this.data.keyword.trim())); },
  bannerChange(event) { this.setData({ bannerIndex: event.detail.current }); },
  catalog(event) {
    const { id, tab } = event.currentTarget.dataset;
    wx.setStorageSync('xa:native:catalog-intent', id ? { gameplay: Number(id) } : tab ? { tab } : {});
    go('/pages/catalog/index');
  },
  companions() { go('/pages/companions/index'); },
  orders(event) { go('/pages/orders/index?status=' + (event.currentTarget.dataset.status || '')); },
  support() { go('/pages/support/index'); },
  rankings() { go('/pages/rankings/index'); },
  coupons() { go('/pages/coupons/index'); },
  announcement(event) { go('/pages/announcement-detail/index?id=' + event.currentTarget.dataset.id); },
  async banner(event) {
    let item = this.data.banners.find(entry => entry.id === Number(event.currentTarget.dataset.id));
    if (!item) return;
    if (item.link_type !== 'NONE') {
      if (this._bannerBusy) return;
      const revision = auth.revision(), id = item.id, previousType = item.link_type, previousValue = item.link_value;
      this._bannerBusy = true;
      try {
        const rows = await request('/banners/');
        if (!this._alive || !this._visible || auth.revision() !== revision) return;
        item = list(rows).find(entry => entry.id === id);
        if (!item) { toast('活动暂不可用，请浏览其他服务'); return; }
        if (item.link_type !== previousType || item.link_value !== previousValue) {
          this.setData({ banners: list(rows).map(entry => ({ ...entry, image: image(entry.image_url) })).filter(entry => entry.image) });
          toast('活动已更新，请重新查看');
          return;
        }
      } catch (_) {
        if (this._alive && this._visible && auth.revision() === revision) toast('活动状态未能更新，请稍后重试');
        return;
      } finally { this._bannerBusy = false; }
    }
    if (item.link_available === false) {
      if (item.link_unavailable_reason === 'QUALIFICATION_REQUIRED') {
        const revision = auth.revision();
        themedModal({ title: '需要套餐资格', content: '请先前往领券中心查看是否有适用的资格券。', confirmText: '查看领券',
          success: result => { if (result.confirm && this._alive && this._visible && auth.revision() === revision) go('/pages/coupons/index'); } });
      } else if (item.link_unavailable_reason === 'QUALIFICATION_USED') toast('该项资格或今日额度已使用');
      else toast('活动暂不可用，请浏览其他服务');
      return;
    }
    if (item.link_type === 'PRODUCT' && /^[1-9]\d*$/.test(item.link_value)) go('/pages/service/index?id=' + item.link_value);
    else if (item.link_type === 'GAMEPLAY' && /^[1-9]\d*$/.test(item.link_value)) go('/pages/catalog/index?gameplay=' + item.link_value);
    else if (item.link_type === 'CONTENT' && /^[1-9]\d*$/.test(item.link_value)) go('/pages/club-content/index?id=' + item.link_value);
    else if (item.link_type === 'PAGE' && ['catalog', 'companions', 'gifts', 'coupons', 'checkin', 'points', 'membership', 'support'].includes(item.link_value)) {
      if (item.link_value === 'catalog') wx.setStorageSync('xa:native:catalog-intent', {});
      go('/pages/' + item.link_value + '/index');
    }
    else if (item.link_type === 'ANNOUNCEMENT' && /^[1-9]\d*$/.test(item.link_value)) go('/pages/announcement-detail/index?id=' + item.link_value);
    else if (item.link_type === 'URL' && /^https:\/\//.test(item.link_value)) themedModal({ title: '查看活动链接', content: '此活动需要在浏览器查看，是否复制活动链接？', confirmText: '复制链接', success: result => { if (result.confirm) wx.setClipboardData({ data: item.link_value }); } });
    else if (item.link_type !== 'NONE') toast('活动入口暂不可用');
  },
  onShareAppMessage() { return { title: '兴安电竞 · 找到适合你的游戏服务', path: '/pages/home/index' }; }
});
