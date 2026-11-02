const api = require('../../core/api');
const auth = require('../../core/auth');
const ui = require('../../core/ui');
const events = require('../../core/events');
const TYPES = { SYSTEM: '系统', ORDER: '订单', SUPPORT: '客服', PROMOTION: '活动' };
const FEED_TYPES = { GIFT: '礼物时刻', NAMING: '专属冠名', RECHARGE: '充值喜报', UPGRADE: 'VIP 升级', REVIEW: '好评时刻', BIRTHDAY: '生日祝福', LOTTERY: '转盘中奖', LOTTERY_SERVICE: '转盘中奖', LOTTERY_NAMING: '转盘中奖', LOTTERY_TAG: '转盘中奖' };

Page({
  data: {
    section: 'feed', feed: [], feedLoading: false, feedError: '', feedMore: false, feedNext: null, feedKind: '', feedLoadingMore: false,
    feedKinds: [{ value: '', label: '全部动态' }, { value: 'GIFT', label: '礼物' }, { value: 'LOTTERY', label: '转盘' }, { value: 'RECHARGE', label: '充值' }, { value: 'REVIEW', label: '好评' }, { value: 'NAMING', label: '冠名' }, { value: 'UPGRADE', label: 'VIP 升级' }, { value: 'BIRTHDAY', label: '生日' }],
    loading: false, loadingMore: false, error: '', moreError: '', items: [], unread: 0,
    page: 0, total: 0, hasMore: false, nextBefore: null, filter: '', marking: false,
    filters: [{ value: '', label: '全部' }, { value: 'ORDER', label: '订单' },
      { value: 'SYSTEM', label: '系统' }, { value: 'SUPPORT', label: '客服' }, { value: 'PROMOTION', label: '活动' }]
  },
  onLoad() {
    this._alive = true;
    const nativeSetData = this.setData.bind(this);
    this.setData = (patch, callback) => { if (this._alive) nativeSetData(patch, callback); };
    this._noticeOff = events.on('site-message.changed', () => { if (this._visible) this.load(); });
    this._feedOff = events.on('feed.changed', () => { if (this._visible) this.loadFeed(); });
    this._sessionOff = events.on('session.changed', () => {
      this._revision = (this._revision || 0) + 1;
      this._feedRevision = (this._feedRevision || 0) + 1;
      this.setData({ feed: [], feedError: '', feedMore: false, feedNext: null, feedLoading: false, feedLoadingMore: false });
      this.setData({ items: [], unread: 0, page: 0, hasMore: false, nextBefore: null, logged: !!auth.getSession() });
      if (this._visible) { this.load(); this.loadFeed(); }
    });
  },
  onShow() { this._visible = true; events.emit('site-message.badge'); this.load(); this.loadFeed(); clearInterval(this._feedTimer); this._feedTimer = setInterval(() => { if (this._visible && this.data.section === 'feed') this.loadFeed(); }, 60000); },
  onHide() { this._visible = false; clearInterval(this._feedTimer); },
  onUnload() { this._alive = false; this._visible = false; clearInterval(this._feedTimer); this._feedOff && this._feedOff(); this._off && this._off(); this._noticeOff && this._noticeOff(); this._sessionOff && this._sessionOff(); this._revision = -1; this._feedRevision = -1; },
  onPullDownRefresh() { return Promise.all([this.load(), this.loadFeed()]).finally(() => wx.stopPullDownRefresh()); },
  onReachBottom() { if (this.data.section === 'feed') this.moreFeed(); else this.loadMore(); },
  section(event) { const section = event.currentTarget.dataset.section; if (['feed', 'private'].includes(section)) this.setData({ section }); },
  feedFilter(event) { this.setData({ feedKind: event.currentTarget.dataset.kind, feed: [], feedMore: false }); this.loadFeed(); },
  loadFeed() { return this.fetchFeed(false); },
  moreFeed() { if (this.data.feedMore && !this.data.feedLoading && !this.data.feedLoadingMore) return this.fetchFeed(true); },
  async fetchFeed(more) {
    if (!this._alive || !auth.getSession()) return;
    const revision = more ? this._feedRevision : (this._feedRevision = (this._feedRevision || 0) + 1);
    this.setData({ feedLoading: !more, feedLoadingMore: !!more, feedError: '' });
    try {
      const result = await api.request('/club/feed/?page=1' + (more ? '&before=' + this.data.feedNext : '') + (this.data.feedKind ? '&kind=' + this.data.feedKind : ''));
      if (!this._alive || revision !== this._feedRevision) return;
      const rows = ui.list(result.items).map(item => ({ ...item, label: FEED_TYPES[item.kind] || '俱乐部动态', time: ui.date(item.created_at), icon: item.kind === 'REVIEW' ? 'heart' : item.kind === 'BIRTHDAY' ? 'calendar' : item.kind === 'GIFT' ? 'heart' : 'award' }));
      const known = new Set(more ? this.data.feed.map(item => item.id) : []);
      this.setData({ feed: (more ? this.data.feed : []).concat(rows.filter(item => !known.has(item.id))), feedMore: !!result.has_more, feedNext: result.next });
    } catch (error) { if (this._alive && revision === this._feedRevision) this.setData({ feedError: error.message || '动态暂未更新，请重试' }); }
    finally { if (this._alive && revision === this._feedRevision) this.setData({ feedLoading: false, feedLoadingMore: false }); }
  },
  async load() {
    if (!this._alive) return;
    const logged = !!auth.getSession();
    this.setData({ logged });
    if (!logged) { this.setData({ loading: false, items: [], unread: 0, hasMore: false, nextBefore: null }); return; }
    const revision = this._revision = (this._revision || 0) + 1;
    const oldestVisible = this.data.items.length ? this.data.items[this.data.items.length - 1].id : null;
    const filter = this.data.filter;
    this.setData({ loading: true, error: '', moreError: '', loadingMore: false });
    try {
      const items = [], known = new Set();
      let before = null, result, first, pages = 0;
      do {
        result = await api.request('/messages/?page_size=20' + (before ? '&before=' + before : '') + (filter ? '&type=' + filter : ''));
        if (revision !== this._revision) return;
        if (!first) first = result;
        const incoming = this.format(result.messages);
        for (const item of incoming) if (!known.has(item.id)) { known.add(item.id); items.push(item); }
        before = result.next_before || null;
        pages += 1;
      } while (oldestVisible && !known.has(oldestVisible) && before);
      this.setData({ items, unread: Number(first.unread) || 0, page: pages, total: Number(first.total) || 0,
        hasMore: !!before, nextBefore: before });
    } catch (error) { if (revision === this._revision) this.setData({ error: error.message || '消息加载失败，请重试' }); }
    finally { if (revision === this._revision) this.setData({ loading: false }); }
  },
  async loadMore() {
    if (!this.data.hasMore || !this.data.nextBefore || this.data.loading || this.data.loadingMore || !auth.ensure()) return;
    const revision = this._revision;
    const page = this.data.page + 1;
    this.setData({ loadingMore: true, moreError: '' });
    try {
      const result = await api.request('/messages/?page_size=20&before=' + this.data.nextBefore + (this.data.filter ? '&type=' + this.data.filter : ''));
      if (revision !== this._revision) return;
      const known = new Set(this.data.items.map(item => item.id));
      const incoming = this.format(result.messages);
      const items = this.data.items.concat(incoming.filter(item => !known.has(item.id)));
      this.setData({ items, page, total: Number(result.total) || 0, unread: Number(result.unread) || 0,
        hasMore: !!result.next_before, nextBefore: result.next_before || null });
    } catch (error) { if (revision === this._revision) this.setData({ moreError: error.message || '加载更多失败' }); }
    finally { if (revision === this._revision) this.setData({ loadingMore: false }); }
  },
  format(items) { return ui.list(items).map(item => Object.assign({}, item, { dateText: ui.date(item.created_at), typeText: TYPES[item.type] || '通知' })); },
  filter(e) {
    const filter = e.currentTarget.dataset.value;
    if (filter === this.data.filter) return;
    this.setData({ filter, items: [], page: 0, hasMore: false, nextBefore: null });
    this.load();
  },
  async markRead() {
    if (!this.data.unread || this.data.marking || !auth.ensure()) return;
    this.setData({ marking: true });
    try { await api.request('/messages/read-all/', 'POST'); if (this._alive) { events.emit('site-message.changed'); ui.toast('已全部标记为已读'); } }
    catch (error) { if (this._alive) ui.toast(error); }
    finally { this.setData({ marking: false }); }
  },
  open(e) { ui.go('/pages/message-detail/index?id=' + e.currentTarget.dataset.id); },
  support() { ui.go('/pages/support/index'); },
  chat() { ui.go('/pages/chat/index'); },
  announcements() { ui.go('/pages/announcements/index'); },
  login() { auth.ensure(); }
});
