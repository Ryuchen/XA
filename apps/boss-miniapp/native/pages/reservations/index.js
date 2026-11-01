const { privatePage } = require('../../core/page');
const { request } = require('../../core/api');
const { go, toast } = require('../../core/ui');
const { present } = require('../../core/reservations');
const events = require('../../core/events');
const listUrl = cursor => '/orders/reservations/?cursor=1&page_size=20' + (cursor
  ? '&before_time=' + encodeURIComponent(cursor.start_time) + '&before_id=' + cursor.id : '');
const page = privatePage({
  data: {items: [], total: 0, moreBusy: false, nextCursor: null},
  async fetch() {
    const lastVisible = this.data.items.length ? this.data.items[this.data.items.length - 1].id : null;
    const seq = this._seq, items = [], seen = new Set();
    let cursor = null, total = 0;
    do {
      const data = await request(listUrl(cursor));
      if (!this._alive || seq !== this._seq) return {};
      total = data.total;
      for (const row of data.items) if (!seen.has(row.id)) { seen.add(row.id); items.push(present(row)); }
      cursor = data.next_cursor;
    } while (lastVisible && !seen.has(lastVisible) && cursor);
    return {items, total, nextCursor: cursor, moreBusy: false};
  },
  open(e) { go('/pages/reservation-detail/index?id=' + e.currentTarget.dataset.id); },
  async more() {
    if (this.data.moreBusy || this.data.loading || !this.data.nextCursor) return;
    this.setData({moreBusy: true}); const seq = this._seq;
    try { const data = await request(listUrl(this.data.nextCursor));
      const seen = new Set(this.data.items.map(row => row.id));
      if (this._alive && seq === this._seq) this.setData({items: this.data.items.concat(data.items.filter(row => !seen.has(row.id)).map(present)), nextCursor: data.next_cursor, total: data.total});
    } catch (error) { if (this._alive && seq === this._seq) toast(error); }
    finally { if (this._alive && seq === this._seq) this.setData({moreBusy: false}); }
  },
  browse() { go('/pages/catalog/index'); },
});
const onLoad = page.onLoad, onUnload = page.onUnload;
page.onLoad = function(options) { onLoad.call(this, options); this._noticeOff = events.on('reservation.changed', () => { if (this._visible) this.load(); }); };
page.onUnload = function() { this._noticeOff?.(); onUnload.call(this); };
Page(page);
