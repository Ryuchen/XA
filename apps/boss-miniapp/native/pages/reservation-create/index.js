const { request } = require('../../core/api');
const auth = require('../../core/auth');
const { on } = require('../../core/events');
const { go, toast } = require('../../core/ui');

const two = n => String(n).padStart(2, '0');
function parts(value) { const local = new Date(value.getTime() + 8 * 3600000); return { day: local.getUTCFullYear() + '-' + two(local.getUTCMonth() + 1) + '-' + two(local.getUTCDate()), time: two(local.getUTCHours()) + ':' + two(local.getUTCMinutes()) }; }
Page({
  data: { logged: false, loading: false, error: '', service: null, provider: null, day: '', time: '', durationIndex: 2,
    durations: Array.from({length: 47}, (_, i) => (i + 2) * 15), rounds: '1', region: '', nickname: '', uid: '', remark: '', busy: false, uncertain: false },
  onLoad(options) {
    this._alive = true; this._providerId = Number(options.provider_id); this._serviceId = Number(options.service_id);
    const now = new Date(), next = new Date(Math.ceil((now.getTime() + 60000) / 900000) * 900000);
    this.setData(parts(next));
    this._off = on('session.changed', () => { ++this._seq; this.setData({service: null, provider: null, rounds: '1', region: '', nickname: '', uid: '', remark: '', busy: false, uncertain: false}); if (this._visible) this.load(); });
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
  day(e) { this.setData({day: e.detail.value}); }, time(e) { this.setData({time: e.detail.value}); },
  duration(e) { this.setData({durationIndex: Number(e.detail.value)}); },
  input(e) { this.setData({[e.currentTarget.dataset.key]: e.detail.value}); },
  async submit() {
    if (this.data.busy || this.data.uncertain || !this.data.service || !this.data.provider || !auth.ensure()) return;
    const start = new Date(this.data.day + 'T' + this.data.time + ':00+08:00'), duration = this.data.durations[this.data.durationIndex];
    const rounds = Number(this.data.rounds);
    if (!Number.isFinite(start.getTime()) || start.getTime() <= Date.now()) return toast('请选择未来的预约时间');
    if (Number(this.data.time.slice(-2)) % 15) return toast('开始时间请按 15 分钟选择');
    if (!Number.isInteger(rounds) || rounds < 1) return toast('请填写有效的服务数量');
    const revision = auth.revision(); this.setData({busy: true});
    try {
      const result = await request('/orders/reservations/', 'POST', {provider_account_id: this._providerId, service_id: this._serviceId,
        start_time: start.toISOString(), end_time: new Date(start.getTime() + duration * 60000).toISOString(), game_rounds: rounds,
        game_region: this.data.region.trim(), game_nickname: this.data.nickname.trim(), game_uid: this.data.uid.trim(), remark: this.data.remark.trim()});
      if (!this._alive || revision !== auth.revision()) return;
      toast('预约已提交，等待陪玩确认');
      wx.redirectTo({url: '/pages/reservation-detail/index?id=' + result.id, fail: () => go('/pages/reservations/index')});
    } catch (error) {
      if (this._alive && revision === auth.revision()) {
        if (error.status === 0 || error.status >= 500) this.setData({uncertain: true});
        else toast(error);
      }
    }
    finally { if (this._alive && revision === auth.revision()) this.setData({busy: false}); }
  },
  login() { auth.ensure(); }, list() { go('/pages/reservations/index'); }, browse() { go('/pages/catalog/index'); },
  reviewed() { this.setData({uncertain: false}); },
});
