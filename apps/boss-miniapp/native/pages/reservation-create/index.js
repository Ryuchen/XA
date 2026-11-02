const { request } = require('../../core/api');
const auth = require('../../core/auth');
const { on } = require('../../core/events');
const { go, toast } = require('../../core/ui');

const two = n => String(n).padStart(2, '0');
function parts(value) { const local = new Date(value.getTime() + 8 * 3600000); return { day: local.getUTCFullYear() + '-' + two(local.getUTCMonth() + 1) + '-' + two(local.getUTCDate()), time: two(local.getUTCHours()) + ':' + two(local.getUTCMinutes()) }; }
Page({
  data: { logged: false, loading: false, error: '', submitError: '', service: null, provider: null, day: '', time: '', durationIndex: 2,
    durations: Array.from({length: 47}, (_, i) => (i + 2) * 15), rounds: '1', region: '', nickname: '', uid: '', remark: '', busy: false, uncertain: false, uncertainTime: '' },
  onLoad(options) {
    this._alive = true; this._providerId = Number(options.provider_id); this._serviceId = Number(options.service_id);
    const now = new Date(), next = new Date(Math.ceil((now.getTime() + 60000) / 900000) * 900000);
    this.setData(parts(next)); this.restorePending();
    this._off = on('session.changed', () => { ++this._seq; this.setData({service: null, provider: null, submitError: '', rounds: '1', region: '', nickname: '', uid: '', remark: '', busy: false}); this.restorePending(); if (this._visible) this.load(); });
  },
  pendingKey() { const session = auth.getSession(); return session ? `xa:native:reservation-pending:${session.user.id}:${this._providerId}:${this._serviceId}` : ''; },
  restorePending() {
    let pending = null;
    try { const key = this.pendingKey(); if (key) pending = wx.getStorageSync(key); } catch (_) {}
    this.setData({uncertain: !!pending, uncertainTime: pending && typeof pending.time === 'string' ? pending.time : ''});
  },
  onShow() { this._visible = true; this.load(); },
  onHide() { this._visible = false; },
  onUnload() { this._alive = false; this._off(); },
  async onPullDownRefresh() { await this.load(); wx.stopPullDownRefresh(); },
  async load() {
    const seq = this._seq = (this._seq || 0) + 1, revision = auth.revision(), logged = !!auth.getSession();
    this.setData({logged, loading: logged, error: ''});
    if (!logged) return;
    try {
      if (!Number.isSafeInteger(this._providerId) || this._providerId < 1 || !Number.isSafeInteger(this._serviceId) || this._serviceId < 1) throw new Error('请先选择服务和陪玩');
      const [service, provider] = await Promise.all([request('/orders/services/' + this._serviceId + '/'), request('/users/escorts/' + this._providerId + '/?service_item=' + this._serviceId)]);
      if (!this._alive || seq !== this._seq || revision !== auth.revision()) return;
      this.setData({service, provider});
    } catch (error) { if (this._alive && seq === this._seq) this.setData({error: error.message}); }
    finally { if (this._alive && seq === this._seq) this.setData({loading: false}); }
  },
  day(e) { this.setData({day: e.detail.value, submitError: ''}); }, time(e) { this.setData({time: e.detail.value, submitError: ''}); },
  duration(e) { this.setData({durationIndex: Number(e.detail.value), submitError: ''}); },
  input(e) { this.setData({[e.currentTarget.dataset.key]: e.detail.value, submitError: ''}); },
  async submit() {
    if (this.data.busy || this.data.uncertain || !this.data.service || !this.data.provider || !auth.ensure()) return;
    const start = new Date(this.data.day + 'T' + this.data.time + ':00+08:00'), duration = this.data.durations[this.data.durationIndex];
    const rounds = Number(this.data.rounds);
    if (!Number.isFinite(start.getTime()) || start.getTime() <= Date.now()) return toast('请选择未来的预约时间');
    if (Number(this.data.time.slice(-2)) % 15) return toast('开始时间请按 15 分钟选择');
    if (!Number.isInteger(rounds) || rounds < 1) return toast('请填写有效的服务数量');
    const revision = auth.revision(), pendingKey = this.pendingKey();
    const pending = {time: this.data.day + ' ' + this.data.time + '（北京时间），' + duration + ' 分钟'};
    try { if (pendingKey) wx.setStorageSync(pendingKey, pending); } catch (_) { /* Keep the current-page warning if local storage is unavailable. */ }
    this.setData({busy: true, submitError: ''});
    try {
      const result = await request('/orders/reservations/', 'POST', {provider_account_id: this._providerId, service_id: this._serviceId,
        start_time: start.toISOString(), end_time: new Date(start.getTime() + duration * 60000).toISOString(), game_rounds: rounds,
        game_region: this.data.region.trim(), game_nickname: this.data.nickname.trim(), game_uid: this.data.uid.trim(), remark: this.data.remark.trim()});
      try { if (pendingKey) wx.removeStorageSync(pendingKey); } catch (_) {}
      if (!this._alive || revision !== auth.revision()) return;
      toast('预约已提交，等待陪玩确认');
      wx.redirectTo({url: '/pages/reservation-detail/index?id=' + result.id, fail: () => go('/pages/reservations/index')});
    } catch (error) {
      if (this._alive && revision === auth.revision()) {
        if (error.status === 0 || error.status >= 500) this.setData({uncertain: true, uncertainTime: pending.time});
        else { try { if (pendingKey) wx.removeStorageSync(pendingKey); } catch (_) {} this.setData({submitError: error.message || '预约未提交，请重新核对'}); toast(error); }
      }
    }
    finally { if (this._alive && revision === auth.revision()) this.setData({busy: false}); }
  },
  login() { auth.ensure(); }, list() { go('/pages/reservations/index'); }, browse() { go('/pages/catalog/index'); },
  reviewed() {
    try { const key = this.pendingKey(); if (key) wx.removeStorageSync(key); }
    catch (_) { toast('无法保存核对结果，请检查设备存储'); return; }
    this.setData({uncertain: false, uncertainTime: ''});
  },
});
