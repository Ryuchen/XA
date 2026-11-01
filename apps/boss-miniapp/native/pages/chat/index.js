const auth = require('../../core/auth');
const { request, upload } = require('../../core/api');
const { on, emit } = require('../../core/events');
const { image, date, toast, modal } = require('../../core/ui');
const messageId = () => 'chat-' + Date.now() + '-' + Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2);
const pendingKey = session => session && session.user && session.user.id != null ? 'xa:native:chat-pending:' + session.user.id : '';
const draftKey = (session, scope) => session && session.user && session.user.id != null ? 'xa:native:chat-draft:' + session.user.id + ':' + scope : '';
Page({
  data: { logged: false, loading: false, error: '', items: [], content: '', sending: false, pendingSend: false, pendingRestored: false, pendingStorageError: false, contextLoading: false, contextError: '', contextOrderNo: '', scrollTo: '', hasMore: false, historyCursor: null, historyLoading: false, historyError: '', historyBrowsing: false },
  onLoad(options) {
    this._alive = true; this._seq = 0; this._historySeq = 0; this._historyStarted = false; this._historyScrollTop = null; this._historyTouchY = null; this._historyUserMovedUp = false; this._pending = null;
    this._contextOrderId = /^[1-9]\d*$/.test(options.order_id || '') ? options.order_id : '';
    this._draftScope = this._contextOrderId || 'general';
    this._contextSeq = 0; this._contextStarted = false; this._contextPrefix = '';
    this._contextOwnerId = auth.getSession()?.user?.id ?? null; this._sessionDraft = null;
    this._off = on('message.changed', () => { if (this._visible) this.load(true); });
    this._sessionOff = on('session.changed', session => {
      this._seq++; this._historySeq++; this._contextSeq++;
      const nextId = session?.user?.id ?? null;
      const sameOwner = nextId != null && this._contextOwnerId != null && String(nextId) === String(this._contextOwnerId);
      if (this.data.logged && this._contextOwnerId != null && (nextId == null || sameOwner)) this._sessionDraft = this.data.content;
      if (nextId != null && this._contextOwnerId != null && !sameOwner) { this._contextOrderId = ''; this._sessionDraft = null; }
      const restoredDraft = nextId != null ? (sameOwner && this._sessionDraft != null ? this._sessionDraft : this.readDraft(session)) : '';
      if (nextId != null) this._sessionDraft = null;
      if (nextId != null) this._contextOwnerId = nextId;
      this._contextStarted = false; this._contextPrefix = '';
      this._historyStarted = false; this._historyScrollTop = null; this._historyTouchY = null; this._historyUserMovedUp = false; this._pending = null;
      this.setData({ items: [], content: restoredDraft, sending: false, pendingSend: false, pendingRestored: false, pendingStorageError: false, contextLoading: false, contextError: '', contextOrderNo: '', logged: !!session, hasMore: false, historyCursor: null, historyLoading: false, historyError: '', historyBrowsing: false, scrollTo: '' });
      this.restorePending();
      if (this._visible) { this.load(); this.loadContext(); }
    });
    this.setData({ content: this.readDraft(auth.getSession()) });
    this.restorePending();
  },
  onShow() { this._visible = true; this.restorePending(); this.load(); this.loadContext(); },
  onHide() { this._visible = false; },
  onUnload() { this._alive = false; this._off(); this._sessionOff(); },
  onPullDownRefresh() { return this.load().finally(() => wx.stopPullDownRefresh()); },
  async loadContext() {
    if (!this._contextOrderId || this._contextStarted || !auth.getSession()) return;
    this._contextStarted = true;
    const seq = ++this._contextSeq, revision = auth.revision(), orderId = this._contextOrderId;
    this.setData({ contextLoading: true, contextError: '' });
    try {
      const order = await request('/orders/orders/' + orderId + '/');
      if (!this._alive || seq !== this._contextSeq || revision !== auth.revision()) return;
      if (!order || typeof order.order_no !== 'string' || !order.order_no) throw new Error('订单号暂不可用');
      const prefix = '我想咨询订单 ' + order.order_no + ' 的服务情况。';
      this._contextPrefix = prefix;
      const draft = this.data.content;
      // Preserve a draft typed after a failed lookup, while making the order
      // visible to the receiving customer-service agent.
      if (!draft) this.setData({ content: prefix, contextOrderNo: order.order_no });
      else if (draft === prefix || draft.startsWith(prefix + '\n')) this.setData({ contextOrderNo: order.order_no });
      else if (prefix.length + 1 + draft.length <= 1000) this.setData({ content: prefix + '\n' + draft, contextOrderNo: order.order_no });
      else this.setData({ contextOrderNo: order.order_no, contextError: '订单号已读取，请精简消息后手动填写。' });
    } catch (_) {
      if (this._alive && seq === this._contextSeq && revision === auth.revision()) this.setData({ contextError: '订单号暂未读取，请重试或手动填写。' });
    } finally {
      if (this._alive && seq === this._contextSeq && revision === auth.revision()) this.setData({ contextLoading: false });
    }
  },
  retryContext() { if (this.data.contextLoading) return; this._contextStarted = false; return this.loadContext(); },
  clearContext() {
    if (this.data.sending || this.data.pendingSend) return;
    this._contextSeq++; this._contextOrderId = '';
    const prefix = this._contextPrefix, content = this.data.content;
    this._contextPrefix = '';
    const draft = prefix && content === prefix ? '' : prefix && content.startsWith(prefix + '\n') ? content.slice(prefix.length + 1) : content;
    this.setData({ content: draft, contextLoading: false, contextError: '', contextOrderNo: '' });
    this.saveDraft(draft);
  },
  async load(quiet = false) {
    const seq = this._seq = (this._seq || 0) + 1, revision = auth.revision(), logged = !!auth.getSession();
    this.setData({ logged, loading: logged && !quiet, error: '' });
    if (!logged) return;
    try {
      let result = await request('/chat/session/');
      if (!this._alive || seq !== this._seq || revision !== auth.revision()) return;
      const existingIds = new Set(this.data.items.map(item => item.id));
      const loaded = [...result.messages];
      // A reconnect can miss more than the newest page. Fetch back to the
      // displayed history so the timeline cannot silently skip replies.
      while (existingIds.size && !loaded.some(item => existingIds.has(item.id)) && result.has_more && result.next_before_id) {
        result = await request('/chat/session/?before_id=' + encodeURIComponent(result.next_before_id));
        if (!this._alive || seq !== this._seq || revision !== auth.revision()) return;
        loaded.push(...result.messages);
      }
      const latest = loaded.map(item => ({ ...item, image: image(item.image_url), time: date(item.created_at) }));
      const items = this.mergeMessages(this.data.items, latest);
      const patch = { items };
      if (!existingIds.size && !this._historyStarted) { patch.hasMore = !!result.has_more; patch.historyCursor = result.next_before_id || null; }
      if (!this.data.historyBrowsing) patch.scrollTo = items.length ? 'message-' + items[items.length - 1].id : '';
      this.setData(patch);
      if (this._visible && !this.data.historyBrowsing && latest.length) await this.markRead(latest, revision);
    } catch (error) { if (this._alive && seq === this._seq) this.setData({ error: error.message }); }
    finally { if (this._alive && seq === this._seq) this.setData({ loading: false }); }
  },
  mergeMessages(existing, incoming) {
    return [...new Map([...existing, ...incoming].map(item => [item.id, item])).values()]
      .sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)) || a.id - b.id);
  },
  async markRead(items, revision) {
    const ids = [...new Set(items.filter(item => item.is_from_support).map(item => Number(item.id)).filter(id => Number.isSafeInteger(id) && id > 0))];
    for (let offset = 0; offset < ids.length; offset += 200) {
      if (!this._alive || !this._visible || revision !== auth.revision()) return;
      await request('/chat/read/', 'POST', { message_ids: ids.slice(offset, offset + 200) });
    }
  },
  onHistoryTouchStart(event) {
    const touch = event.touches && event.touches[0];
    this._historyTouchY = touch ? Number(touch.clientY ?? touch.pageY) : null;
    this._historyUserMovedUp = false;
  },
  onHistoryTouchMove(event) {
    const touch = event.touches && event.touches[0];
    const y = touch ? Number(touch.clientY ?? touch.pageY) : NaN;
    if (Number.isFinite(y) && Number.isFinite(this._historyTouchY) && y - this._historyTouchY > 8) this._historyUserMovedUp = true;
  },
  onHistoryTouchEnd() { this._historyTouchY = null; this._historyUserMovedUp = false; },
  onHistoryScroll(event) {
    const top = Number(event.detail && event.detail.scrollTop);
    if (!Number.isFinite(top)) return;
    const movingUp = this._historyUserMovedUp || (this._historyScrollTop !== null && top < this._historyScrollTop);
    this._historyScrollTop = top;
    this._historyUserMovedUp = false;
    if (movingUp && !this.data.historyBrowsing) this.setData({ historyBrowsing: true, scrollTo: '' });
  },
  async loadOlder() {
    if (!this.data.hasMore || this.data.historyLoading || !this.data.historyCursor || !auth.getSession()) return;
    const seq = ++this._historySeq, revision = auth.revision(), before = this.data.historyCursor;
    const anchor = this.data.items[0]?.id;
    this.setData({ historyLoading: true, historyError: '' });
    try {
      const result = await request('/chat/session/?before_id=' + encodeURIComponent(before));
      if (!this._alive || seq !== this._historySeq || revision !== auth.revision()) return;
      const older = result.messages.map(item => ({ ...item, image: image(item.image_url), time: date(item.created_at) }));
      this._historyStarted = true;
      this.setData({ items: this.mergeMessages(older, this.data.items), hasMore: !!result.has_more,
        historyCursor: result.next_before_id || null, historyBrowsing: true, scrollTo: anchor ? 'message-' + anchor : '' });
    } catch (error) { if (this._alive && seq === this._historySeq && revision === auth.revision()) this.setData({ historyError: error.message || '聊天记录暂时无法加载' }); }
    finally { if (this._alive && seq === this._historySeq && revision === auth.revision()) this.setData({ historyLoading: false }); }
  },
  async latest() {
    const revision = auth.revision();
    const items = this.data.items;
    this.setData({ historyBrowsing: false, scrollTo: items.length ? 'message-' + items[items.length - 1].id : '' });
    if (this._visible && items.length) {
      try { await this.markRead(items, revision); }
      catch (error) { if (this._alive && this._visible && revision === auth.revision()) toast(error); }
    }
  },
  readDraft(session, scope = this._draftScope) {
    const key = draftKey(session, scope);
    if (!key) return '';
    try { const value = wx.getStorageSync(key); return typeof value === 'string' ? value : ''; } catch (_) { return ''; }
  },
  saveDraft(content, scope = this._draftScope) {
    const key = draftKey(auth.getSession(), scope);
    if (!key) return;
    try { if (content) wx.setStorageSync(key, content); else wx.removeStorageSync(key); } catch (_) { /* Current page still retains the text. */ }
  },
  input(event) { const content = event.detail.value; this.setData({ content }); this.saveDraft(content); },
  restorePending() {
    if (this._pending) return;
    const key = pendingKey(auth.getSession());
    if (!key) return;
    try {
      const value = wx.getStorageSync(key);
      if (value && typeof value.id === 'string' && /^[a-zA-Z0-9_-]{8,64}$/.test(value.id) && ['text', 'image'].includes(value.kind)) {
        this._pending = { id: value.id, kind: value.kind, draftScope: typeof value.draftScope === 'string' && /^(general|[1-9]\d*)$/.test(value.draftScope) ? value.draftScope : this._draftScope, key, restored: true, stored: true };
        this.setData({ pendingSend: true, pendingRestored: true, pendingStorageError: false });
      }
    } catch (_) { /* The current page can still send without local storage. */ }
  },
  savePending(pending) {
    pending.key = pendingKey(auth.getSession());
    pending.stored = false;
    if (!pending.key) return;
    try { wx.setStorageSync(pending.key, { id: pending.id, kind: pending.kind, draftScope: pending.draftScope }); pending.stored = true; } catch (_) { /* Keep the user on this page for recovery. */ }
  },
  clearPending(pending) {
    if (!pending || !pending.key) return;
    try { if (wx.getStorageSync(pending.key)?.id === pending.id) wx.removeStorageSync(pending.key); } catch (_) { /* A later read can recover the same receipt. */ }
  },
  async finishPending(pending, message, revision) {
    if (!this._alive || revision !== auth.revision() || this._pending !== pending || !message || message.client_message_id !== pending.id) return false;
    this.clearPending(pending); this._pending = null;
    const patch = { pendingSend: false, pendingRestored: false, pendingStorageError: false, historyBrowsing: false };
    if (pending.kind === 'text') {
      const sentContent = pending.content || message.content, scope = pending.draftScope || this._draftScope;
      if (this.readDraft(auth.getSession(), scope).trim() === sentContent) this.saveDraft('', scope);
      if (scope === this._draftScope && this.data.content.trim() === sentContent) patch.content = '';
    }
    this.setData(patch);
    await this.load(true);
    if (this._alive && revision === auth.revision()) emit('message.changed');
    return true;
  },
  async checkPending() {
    const pending = this._pending, revision = auth.revision();
    if (!pending || this.data.sending || !auth.ensure()) return;
    this.setData({ sending: true });
    try {
      const result = await request('/chat/message-status/?client_message_id=' + encodeURIComponent(pending.id));
      if (!this._alive || revision !== auth.revision() || this._pending !== pending) return;
      if (result.status === 'FOUND' && await this.finishPending(pending, result.message, revision)) toast('消息已发送');
      else toast(pending.restored ? '暂未查到结果，请稍后再核对' : '暂未查到结果，可重试原消息');
    } catch (_) { if (this._alive && revision === auth.revision()) toast('暂时无法核对，请稍后重试'); }
    finally { if (this._alive && revision === auth.revision()) this.setData({ sending: false }); }
  },
  abandonPending() {
    if (!this._pending || this.data.sending) return;
    const pending = this._pending, revision = auth.revision();
    modal({ title: '放弃核对？', content: '这条消息可能已经送达。可先查看聊天记录，再决定是否发送新消息。', confirmText: '放弃核对', success: answer => {
      if (answer.confirm && this._alive && revision === auth.revision() && this._pending === pending) { this.clearPending(pending); this._pending = null; this.setData({ pendingSend: false, pendingRestored: false, pendingStorageError: false }); }
    }});
  },
  async retryPending() {
    const pending = this._pending, revision = auth.revision();
    if (!pending || pending.restored || this.data.sending || !auth.ensure()) return;
    this.setData({ sending: true });
    try {
      const message = pending.kind === 'text'
        ? await request('/chat/send/', 'POST', { content: pending.content, client_message_id: pending.id })
        : await upload('/chat/send/', pending.path, 'image', { content: pending.content, client_message_id: pending.id });
      if (!await this.finishPending(pending, message, revision) && this._alive && revision === auth.revision()) toast('消息结果未确认，请核对');
    } catch (error) {
      if (!this._alive || revision !== auth.revision() || this._pending !== pending) return;
      // A known validation refusal leaves the original message available for
      // correction; transport and server failures may have committed.
      if (error && error.code >= 400 && error.code < 500 && error.code !== 409 && error.code !== 429 && error.status !== 0) {
        this.clearPending(pending); this._pending = null; this.setData({ pendingSend: false, pendingRestored: false, pendingStorageError: false }); toast(error);
      } else { this.setData({ pendingSend: true, pendingStorageError: !pending.stored }); toast('发送结果未确认，请先核对'); await this.load(true); }
    } finally { if (this._alive && revision === auth.revision()) this.setData({ sending: false }); }
  },
  async send() {
    if (!auth.ensure() || this.data.sending || this.data.pendingSend || this.data.contextLoading || !this.data.content.trim()) return;
    this.saveDraft(this.data.content);
    this._pending = { id: messageId(), kind: 'text', content: this.data.content.trim(), draftScope: this._draftScope };
    this.savePending(this._pending);
    return this.retryPending();
  },
  chooseImage() {
    if (!auth.ensure() || this.data.sending || this.data.pendingSend || this.data.contextLoading) return;
    const revision = auth.revision();
    wx.chooseMedia({ count: 1, mediaType: ['image'], sizeType: ['compressed'], sourceType: ['album', 'camera'], success: result => { if (this._alive && revision === auth.revision()) this.sendImage(result.tempFiles[0]); } });
  },
  async sendImage(file) {
    if (!file || this.data.sending || this.data.pendingSend || !this._alive) return;
    if (file.size > 5 * 1024 * 1024) { toast('图片大小不能超过 5MB'); return; }
    const revision = auth.revision();
    this.setData({ sending: true });
    let prepared;
    try { prepared = await require('../../core/image-upload').prepareImage(file.tempFilePath); }
    catch (error) { if (this._alive && revision === auth.revision()) toast(error); return; }
    finally { if (this._alive && revision === auth.revision()) this.setData({ sending: false }); }
    if (!this._alive || revision !== auth.revision()) return;
    const orderNo = this.data.contextOrderNo;
    // Keep the prepared file for the lifetime of this page so retry uses the
    // same image bytes and client message ID.
    this._pending = { id: messageId(), kind: 'image', path: prepared, content: orderNo ? '订单号 ' + orderNo : '' };
    this.savePending(this._pending);
    return this.retryPending();
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
