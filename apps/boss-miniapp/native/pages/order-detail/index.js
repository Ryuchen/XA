const { modal: themedModal } = require('../../core/ui');
const { request } = require('../../core/api');
const auth = require('../../core/auth');
const { present } = require('../../core/orders');
const { on, emit } = require('../../core/events');
const { go, toast, date } = require('../../core/ui');
const aftersaleStatus = { OPEN: '处理中', RESOLVED: '已解决', PARTIALLY_REFUNDED: '已部分退款', REFUNDED: '已退款' };
const targetKey = target => target.provider_account_id ? 'a:' + target.provider_account_id : 'u:' + target.provider_legacy_id;
const isPastAftersale = (item, current) => item.status !== 'OPEN' && (current.client_request_id ? item.client_request_id !== current.client_request_id : !current.created_at || Date.parse(item.created_at) !== Date.parse(current.created_at));
Page({
  data: { logged: false, loading: false, error: '', order: null, aftersale: null, aftersaleError: '', busy: false, score: 5, content: '', anonymous: false, evaluationTarget: '', gifts: [], giftIndex: 0, giftError: '' },
  onLoad(options) {
    this._id = Number(options.id) || 0; this._alive = true; this._pendingOrderRefresh = false; this._operationSeq = 0; this._evaluationDrafts = {}; this._aftersaleDraft = '';
    this._off = on('session.changed', () => { this._seq++; this._operationSeq++; clearTimeout(this._deadlineTimer); this._pendingOrderRefresh = false; this._aftersaleRequestId = ''; this._aftersaleDraft = ''; this._evaluationDrafts = {}; this.setData({ order: null, aftersale: null, aftersaleError: '', gifts: [], content: '', score: 5, anonymous: false, evaluationTarget: '', busy: false }); if (this._visible) this.load(); });
    this._orderOff = on('order.changed', () => {
      if (!this._visible) return;
      if (this.data.busy) this._pendingOrderRefresh = true;
      else this.load();
    });
  },
  onShow() { this._visible = true; this.load(); },
  onHide() { this._visible = false; clearTimeout(this._deadlineTimer); },
  onUnload() { this._alive = false; clearTimeout(this._deadlineTimer); this._off(); this._orderOff(); },
  onPullDownRefresh() { return this.load().finally(() => wx.stopPullDownRefresh()); },
  scheduleDeadlineRefresh(order) {
    clearTimeout(this._deadlineTimer);
    if (!this._visible || order.status !== 'PENDING' || order.payment_status !== 'PAID' || order.pending_deadline_expired) return;
    const deadline = Date.parse(order.auto_cancel_at || '');
    if (!Number.isFinite(deadline)) return;
    // The device clock only schedules a re-read; server state decides expiry.
    // A skewed clock in the past must not cause a tight request loop.
    const remaining = deadline - Date.now();
    const delay = remaining <= 0 ? 60000 : Math.min(Math.max(remaining + 500, 1000), 60000);
    this._deadlineTimer = setTimeout(() => {
      if (!this._alive || !this._visible) return;
      if (this.data.busy) this._pendingOrderRefresh = true;
      else this.load();
    }, delay);
  },
  async load() {
    this._pendingOrderRefresh = false;
    const seq = this._seq = (this._seq || 0) + 1, logged = !!auth.getSession();
    this.setData({ logged, loading: logged, error: '' });
    if (!logged) return;
    try {
      if (!this._id) throw new Error('缺少订单编号');
      const order = await request('/orders/orders/' + this._id + '/');
      let aftersale = null, aftersaleError = '';
      if (order.aftersale_status) {
        try { aftersale = await request('/orders/orders/' + this._id + '/aftersale/'); }
        catch (error) { aftersaleError = error.message || '售后详情加载失败，请重试'; }
      }
      if (!this._alive || seq !== this._seq) return;
      // A lost POST response can still be confirmed by this authoritative read.
      // Retire its key once observed so a later, genuinely new case can reopen.
      if (this._aftersaleRequestId && aftersale) {
        const matched = aftersale.client_request_id === this._aftersaleRequestId ? aftersale :
          (Array.isArray(aftersale.history) ? aftersale.history.find(item => item.client_request_id === this._aftersaleRequestId) : null);
        if (matched) {
          this._aftersaleRequestId = '';
          if (!this._aftersaleDraft || matched.reason === this._aftersaleDraft) this._aftersaleDraft = '';
        }
      }
      if (aftersale) aftersale = Object.assign({}, aftersale, { history: (Array.isArray(aftersale.history) ? aftersale.history : []).filter(item => isPastAftersale(item, aftersale)).map((item, index) => Object.assign({}, item, { historyKey: item.client_request_id || index, statusText: aftersaleStatus[item.status] || item.status, createdText: date(item.created_at), resolvedText: date(item.resolved_at) })) });
      const confirmed = !!order.consumption_confirmed_at;
      const hasActiveClock = order.status === 'COMPLETED' && ['PAID', 'PARTIALLY_REFUNDED'].includes(order.payment_status) && !!order.consumption_ready_at;
      const consumptionLabel = confirmed ? '实销确认时间' : hasActiveClock ? '实销计时节点' : '';
      const consumptionNote = order.aftersale_status === 'OPEN'
        ? confirmed ? '售后处理中，等待平台处理结果。' : '售后处理中，实销暂不确认；处理结束后仍需满足确认条件。'
        : !confirmed && hasActiveClock ? '达到计时节点后，仍需满足报单审核及售后等条件。' : '';
      const displayOrder = present(order, { readinessValidated: true });
      const targets = Array.isArray(order.evaluation_targets) ? order.evaluation_targets.map(item => ({ ...item, key: targetKey(item) })) : [];
      const selected = targets.find(item => item.key === this.data.evaluationTarget && !item.reviewed) || targets.find(item => !item.reviewed);
      const selectedKey = selected ? selected.key : '';
      const draft = selectedKey !== this.data.evaluationTarget ? (this._evaluationDrafts[selectedKey] || { score: 5, content: '', anonymous: false }) : null;
      displayOrder.evaluationTargets = targets;
      const canAfterSale = order.payment_display?.kind !== 'DIRECT_TEST' && order.can_aftersale === true && order.status === 'COMPLETED' && ['PAID', 'PARTIALLY_REFUNDED'].includes(order.payment_status) && order.aftersale_status !== 'REFUNDED';
      const aftersaleUnavailableNote = !displayOrder.isGift && order.status === 'COMPLETED' && ['PAID', 'PARTIALLY_REFUNDED'].includes(order.payment_status) && order.can_aftersale === false && order.aftersale_status !== 'OPEN' && order.aftersale_status !== 'REFUNDED'
        ? '暂不可在线申请售后，可联系订单客服核对。' : '';
      this.setData({ aftersale, aftersaleError, evaluationTarget: selectedKey, ...(draft || {}), order: Object.assign(displayOrder, { aftersaleStatusText: aftersaleStatus[order.aftersale_status] || '', consumptionLabel, consumptionNote, aftersaleUnavailableNote, consumptionReadyText: date(order.consumption_confirmed_at || order.consumption_ready_at), canAfterSale }) });
      this.scheduleDeadlineRefresh(order);
    } catch (error) { if (this._alive && seq === this._seq) this.setData({ error: error.message, order: null }); }
    finally { if (this._alive && seq === this._seq) this.setData({ loading: false }); }
  },
  async operate(action, uncertainText) {
    if (!auth.ensure() || this.data.busy || !this.data.order) return;
    const revision = auth.revision();
    const operation = ++this._operationSeq;
    this.setData({ busy: true });
    try { await action(); if (this._alive && revision === auth.revision() && operation === this._operationSeq) { emit('order.changed'); await this.load(); } }
    catch (error) {
      if (this._alive && revision === auth.revision()) {
        if (['AFTERSALE_ALREADY_OPEN', 'AFTERSALE_REQUEST_CONFLICT'].includes(error.errorCode)) {
          await new Promise(resolve => themedModal({ title: '售后未提交', content: error.message,
            showCancel: false, confirmText: '知道了', success: resolve, fail: resolve }));
        } else toast(uncertainText && (error.status === 0 || error.status >= 500) ? uncertainText : error);
        if (this._visible) await this.load();
      }
    }
    finally {
      if (this._alive && operation === this._operationSeq) {
        this.setData({ busy: false });
        if (this._visible && this._pendingOrderRefresh) await this.load();
      }
    }
  },
  selectProvider(event) {
    if (!this.data.order.canSelectProvider || this.data.busy) return;
    const row = this.data.order.applications.find(item => item.id === Number(event.currentTarget.dataset.id));
    if (!row || !row.ready) return;
    const revision = auth.revision();
    return this.operate(async () => {
      const answer = await new Promise(resolve => themedModal({ title: '选择本次陪玩', content: row.members.map(member => member.nickname + ' · ' + member.level).join(' + ') + '，共' + row.team_size + '人。按本单已付总金额提供服务。', confirmText: '确认选择', success: resolve, fail: () => resolve({ confirm: false }) }));
      if (answer.confirm && this._alive && revision === auth.revision()) {
        await request('/orders/orders/' + this._id + '/applications/' + row.id + '/select/', 'POST');
        toast('已选择陪玩');
      }
    }, '选择结果未确认，请核对订单状态');
  },
  cancel() {
    if (!this.data.order.canCancel || this.data.busy) return;
    const revision = auth.revision();
    return this.operate(async () => {
      const confirmed = await new Promise(resolve => themedModal({ title: '取消订单', content: (this.data.order.amount === 0 ? '订单仍待接单时可以取消；本单实付为 0，无需退回余额。' : '订单仍待接单时可以取消，已付款金额将退回兴安币余额。') + (this.data.order.cancel_coupon_nonrefundable === true ? '本单已核销的券取消后不返还。' : ''), confirmText: '继续取消', success: resolve, fail: () => resolve({ confirm: false }) }));
      if (!confirmed.confirm || !this._alive || revision !== auth.revision()) return;
      const answer = await new Promise(resolve => themedModal({ title: '取消原因（选填）', editable: true, placeholderText: '可填写取消原因', confirmText: '确认取消', success: resolve, fail: () => resolve({ confirm: false }) }));
      if (answer.confirm && this._alive && revision === auth.revision()) { await request('/orders/orders/' + this._id + '/cancel/', 'POST', { reason: (answer.content || '').slice(0, 255) }); toast(this.data.order.amount === 0 ? '订单已取消' : '订单已取消，请查看退款流水'); }
    }, '取消结果未确认，请查看订单状态');
  },
  evaluationDraft(patch) {
    const draft = { score: this.data.score, content: this.data.content, anonymous: this.data.anonymous, ...patch };
    if (this.data.evaluationTarget) this._evaluationDrafts[this.data.evaluationTarget] = draft;
    this.setData(draft);
  },
  selectEvaluationTarget(event) {
    const key = event.currentTarget.dataset.key;
    if (this.data.busy || !this.data.order.evaluationTargets.some(item => item.key === key && !item.reviewed)) return;
    const draft = this._evaluationDrafts[key] || { score: 5, content: '', anonymous: false };
    this.setData({ evaluationTarget: key, ...draft });
  },
  score(event) { this.evaluationDraft({ score: Number(event.detail.value) }); },
  content(event) { this.evaluationDraft({ content: event.detail.value }); },
  anonymous(event) { this.evaluationDraft({ anonymous: event.detail.value }); },
  evaluate() {
    if (!this.data.order.canEvaluate) return;
    const target = this.data.order.evaluationTargets.find(item => item.key === this.data.evaluationTarget && !item.reviewed);
    if (this.data.order.evaluationTargets.length && !target) return;
    const key = this.data.evaluationTarget;
    const identity = target ? (target.provider_account_id ? { provider_account_id: target.provider_account_id } : { provider_legacy_id: target.provider_legacy_id }) : {};
    return this.operate(async () => {
      await request('/orders/evaluate/', 'POST', { order_id: this._id, ...identity, score: this.data.score, content: this.data.content.trim(), is_anonymous: this.data.anonymous });
      delete this._evaluationDrafts[key];
      this.setData({ evaluationTarget: '', score: 5, content: '', anonymous: false });
      toast('评价已提交');
    }, '评价结果未确认，请查看订单评价');
  },
  tip() {
    if (!this.data.order.canTip || this.data.busy) return;
    go('/pages/gifts/index?order_id=' + this._id);
  },
  aftersale() {
    if (!this.data.order.canAfterSale || this.data.busy) return;
    const revision = auth.revision();
    if (!this._aftersaleRequestId) this._aftersaleRequestId = 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, token => { const value = Math.floor(Math.random() * 16); return (token === 'x' ? value : (value & 3) | 8).toString(16); });
    return this.operate(async () => {
      const answer = await new Promise(resolve => themedModal({ title: '提交订单售后', editable: true, ...(this._aftersaleDraft ? { content: this._aftersaleDraft } : {}), placeholderText: '请填写至少2字问题说明', success: resolve, fail: () => resolve({ confirm: false }) }));
      if (answer.confirm && this._alive && this._visible && revision === auth.revision()) {
        const reason = (answer.content || '').trim();
        if (Array.from(reason).length < 2) { this._aftersaleDraft = reason; this._aftersaleRequestId = ''; toast('请填写至少2字问题说明'); return; }
        this._aftersaleDraft = reason;
        await request('/orders/orders/' + this._id + '/aftersale/', 'POST', { reason, client_request_id: this._aftersaleRequestId });
        if (this._alive && revision === auth.revision()) { this._aftersaleRequestId = ''; this._aftersaleDraft = ''; toast('售后已提交'); }
      }
      else if (revision === auth.revision()) this._aftersaleRequestId = '';
    }, '售后结果未确认，请查看处理状态');
  },
  copy() { wx.setClipboardData({ data: this.data.order.order_no }); },
  support() { go('/pages/support/index?order_id=' + this._id); },
  reservation() { if (this.data.order?.reservationOrigin?.id) go('/pages/reservation-detail/index?id=' + this.data.order.reservationOrigin.id); },
  sourceOrder() { if (this.data.order?.source_order) go('/pages/order-detail/index?id=' + this.data.order.source_order); },
  wallet() { go('/pages/wallet/index'); },
  coupons() { go('/pages/coupons/index?tab=mine'); },
  giftClaim(event) { const id = Number(event.currentTarget.dataset.id); if (Number.isSafeInteger(id) && id > 0) go('/pages/reward-claim/index?id=' + id); },
  login() { auth.ensure(); },
});
