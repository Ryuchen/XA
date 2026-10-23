const { request, fund } = require('../../core/api');
const auth = require('../../core/auth');
const { on } = require('../../core/events');
const { go, toast } = require('../../core/ui');
const { campaignView, resultView } = require('./presentation');

const SIMPLE_MOTION_KEY = 'xa:native:lottery-simple-motion';
const pendingKey = () => 'xa:native:lottery-pending:' + (auth.getSession()?.user.id || 'guest');

Page({
  data: { loading: false, error: '', drawError: '', campaign: null, points: 0, busy: false,
    phase: 'idle', rotation: 0, results: [], revealed: 0, drawCount: 0, resultCount: 0,
    resultRare: false, resultPity: false, resultTenGuarantee: false, resultNonPoints: 0, focusRarity: 'R', simpleMotion: false,
    retryPending: false, retryCount: 0, credentialError: false, size: 300 },
  onLoad() {
    this._alive = true; this._seq = 0; this._angle = 0; this._timers = [];
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
    let simpleMotion = false;
    try { simpleMotion = wx.getStorageSync(SIMPLE_MOTION_KEY) === true; } catch (_) {}
    this.setData({ size: Math.max(220, Math.min(320, info.windowWidth - 104)), simpleMotion });
    this.restorePending();
    this._off = on('session.changed', () => {
      this._seq++; this.clearAnimation(); this._response = null; this._pendingIntent = null;
      this.setData({ campaign: null, points: 0, results: [], resultCount: 0, revealed: 0, busy: false, resultTenGuarantee: false, resultNonPoints: 0,
        phase: 'idle', drawError: '', retryPending: false, retryCount: 0, credentialError: false });
      this.restorePending();
      if (this._visible) this.load();
    });
  },
  onShow() { this._visible = true; if (!this.data.busy) this.load(); },
  onHide() { this._visible = false; if (this._response) this.skipAnimation(); },
  onUnload() { this._alive = false; this.clearAnimation(); this._off(); },
  async onPullDownRefresh() { if (!this.data.busy) await this.load(); wx.stopPullDownRefresh(); },
  restorePending() {
    this._pendingIntent = null;
    this.setData({ credentialError: false, retryPending: false, retryCount: 0 });
    try {
      const value = wx.getStorageSync(pendingKey());
      if (value && Number.isInteger(value.campaign_id) && value.campaign_id > 0 && Number.isInteger(value.version) && value.version > 0 && [1, 10].includes(value.count) && typeof value.request_id === 'string' && /^[a-zA-Z0-9_-]{8,64}$/.test(value.request_id)) {
        this._pendingIntent = { campaign_id: value.campaign_id, version: value.version, count: value.count, request_id: value.request_id };
        this.setData({ retryPending: true, retryCount: value.count,
          drawError: '上次抽奖结果尚未确认。请重试同一笔抽奖，不会重复扣除积分，也可先查看中奖记录。' });
      } else if (value) throw new Error('invalid intent');
    } catch (_) { this.setData({ credentialError: true, retryPending: true, drawError: '无法读取抽奖凭证，请检查设备存储后重新读取，或先查看中奖记录。' }); }
  },
  reloadCredential() { this.setData({ drawError: '' }); this.restorePending(); },
  clearPending() {
    this._pendingIntent = null;
    try { wx.removeStorageSync(pendingKey()); } catch (_) { /* a retained intent remains safe to replay through fund */ }
    this.setData({ retryPending: false, retryCount: 0 });
  },
  async load() {
    if (this.data.busy) return;
    const seq = ++this._seq;
    if (!auth.ensure()) return;
    this.setData({ loading: true, error: '' });
    try {
      const [campaign, points] = await Promise.all([request('/club/lottery/'), request('/club/points/')]);
      if (!this._alive || seq !== this._seq) return;
      this.setData({ campaign: campaignView(campaign), points: points.points, loading: false }, () => this.paint());
    } catch (e) { if (this._alive && seq === this._seq) this.setData({ loading: false, error: e.message }); }
  },
  paint() {
    const prizes = this.data.campaign?.prizes || [];
    if (!prizes.length) return;
    const size = this.data.size, c = size / 2, r = c - 4, step = Math.PI * 2 / prizes.length;
    const ctx = wx.createCanvasContext('wheel', this);
    prizes.forEach((item, i) => {
      const mid = -Math.PI / 2 + i * step;
      ctx.beginPath(); ctx.moveTo(c, c); ctx.arc(c, c, r, mid - step / 2, mid + step / 2); ctx.closePath();
      ctx.setFillStyle(item.rarity === 'SSR' ? '#f3e8c8' : i % 2 ? '#dcebf3' : '#f5f8fa'); ctx.fill();
      ctx.save(); ctx.translate(c, c); ctx.rotate(mid); ctx.setFillStyle('#003b72');
      ctx.setFontSize(10); ctx.setTextAlign('center');
      const label = item.name.replace('任选', '').replace('积分', '分').replace('自定义', '');
      ctx.fillText(label.slice(0, 11), r * .69, 4); ctx.restore();
    });
    ctx.beginPath(); ctx.arc(c, c, r, 0, Math.PI * 2); ctx.setStrokeStyle('#136398'); ctx.setLineWidth(4); ctx.stroke(); ctx.draw();
  },
  spin() { return this.draw(1); },
  ten() { return this.draw(10); },
  retryDraw() { if (this._pendingIntent) return this.draw(this._pendingIntent.count, true); },
  async draw(count, retry = false) {
    const campaign = this.data.campaign;
    if (!auth.ensure() || this.data.busy) return;
    if (!retry && (this.data.retryPending || !campaign?.enabled)) return;
    const intent = retry ? this._pendingIntent : { campaign_id: campaign.id, version: campaign.version, count,
      request_id: 'native-lottery-' + Date.now() + '-' + Math.random().toString(36).slice(2, 12) };
    if (!intent) return;
    const payload = { campaign_id: intent.campaign_id, version: intent.version, count: intent.count };
    if (!retry && count === 10 && !campaign.ten_guarantee.available) return toast(campaign.ten_guarantee.unavailable_reason);
    if (!retry && Number.isInteger(campaign.daily_remaining) && campaign.daily_remaining < count) return toast('今日剩余 ' + campaign.daily_remaining + ' 次，无法' + (count === 10 ? '十连抽' : '继续抽奖'));
    if (!retry && this.data.points < campaign.point_cost * count) return toast('积分不足');
    const seq = this._seq, version = auth.revision();
    try { wx.setStorageSync(pendingKey(), intent); }
    catch (_) { return toast('无法保存抽奖凭证，请检查设备存储后重试'); }
    this._pendingIntent = intent; this._response = null; this.clearAnimation();
    this.setData({ busy: true, phase: 'request', drawError: '', results: [], revealed: 0, resultCount: 0,
      drawCount: payload.count, resultRare: false, resultPity: false, resultTenGuarantee: false, resultNonPoints: 0, retryPending: false });
    let accepted = false;
    try {
      const response = await fund('/club/lottery/', payload, { clientRequestId: intent.request_id });
      if (!this._alive || seq !== this._seq || version !== auth.revision()) return;
      accepted = true;
      const results = resultView(response, payload.count);
      this.clearPending();
      const currentCampaign = this.data.campaign;
      const nextCampaign = currentCampaign?.id === payload.campaign_id ? campaignView({ ...currentCampaign, pity: response.pity,
        ...('daily_remaining' in response ? { daily_remaining: response.daily_remaining } : {}),
        ...('ten_guarantee' in response ? { ten_guarantee: response.ten_guarantee } : {}) }) : currentCampaign;
      this._response = response;
      this.setData({ points: response.points_balance, campaign: nextCampaign, results,
        resultCount: results.length, resultRare: results.some(row => row.rarity === 'SSR'),
        resultPity: results.some(row => row.is_pity), resultTenGuarantee: results.some(row => row.is_ten_guarantee), resultNonPoints: results.filter(row => row.rarity !== 'R').length,
        focusRarity: results.some(row => row.rarity === 'SSR') ? 'SSR' : results.some(row => row.rarity === 'SR') ? 'SR' : 'R' });
      if (this.data.simpleMotion || !this._visible) return this.skipAnimation();
      this.startAnimation(seq);
    } catch (e) {
      if (!this._alive || seq !== this._seq || version !== auth.revision()) return;
      // A successful reply may have consumed the fund credential; never repair its display with a new draw.
      if (accepted) {
        this.clearPending();
        this.setData({ busy: false, phase: 'idle', drawError: '本次抽奖已受理，展示结果暂不可用。请到中奖记录核对并领取奖品。' });
        await this.load();
      } else {
        const definitive = e.status >= 400 && e.status < 500 && ![409, 429].includes(e.status);
        if (definitive) this.clearPending();
        this.setData({ busy: false, phase: 'idle', retryPending: !definitive, retryCount: payload.count,
          drawError: definitive ? e.message : e.message + '。请重试同一笔抽奖，不会重复扣除积分。' });
      }
    }
  },
  startAnimation(seq) {
    const prizes = this.data.campaign?.prizes || [];
    const focus = this.data.results.find(row => row.rarity === this.data.focusRarity) || this.data.results[0];
    const index = prizes.findIndex(row => row.id === focus.prize_id);
    const target = index < 0 ? 0 : (360 - index * 360 / prizes.length) % 360;
    this._angle += 1440 + ((target - this._angle % 360 + 360) % 360);
    this.setData({ phase: 'summon', rotation: this._angle });
    this.later(() => { this.setData({ phase: 'reveal' }, () => this.showResults()); this.revealNext(seq); }, 2100, seq);
  },
  later(action, delay, seq) {
    const timer = setTimeout(() => { if (this._alive && seq === this._seq && this._response) action(); }, delay);
    this._timers.push(timer);
  },
  revealNext(seq) {
    const revealed = this.data.revealed + 1;
    this.setData({ revealed });
    if (revealed >= this.data.resultCount) this.later(() => this.finishAnimation(), 300, seq);
    else this.later(() => this.revealNext(seq), this.data.results[revealed - 1].rarity === 'SSR' ? 460 : 130, seq);
  },
  clearAnimation() { (this._timers || []).forEach(clearTimeout); this._timers = []; },
  finishAnimation() {
    const showResults = this.data.phase !== 'reveal';
    this.clearAnimation(); this._response = null;
    this.setData({ busy: false, phase: 'done', revealed: this.data.results.length }, () => { if (showResults) this.showResults(); });
  },
  showResults() { if (this._visible && wx.pageScrollTo) wx.pageScrollTo({ selector: '#lottery-results', duration: this.data.simpleMotion ? 0 : 250, fail() {} }); },
  skipAnimation() { if (this._response) this.finishAnimation(); },
  toggleMotion(event) {
    const simpleMotion = !!event.detail.value;
    try { wx.setStorageSync(SIMPLE_MOTION_KEY, simpleMotion); } catch (_) {}
    this.setData({ simpleMotion });
    if (simpleMotion) this.skipAnimation();
  },
  claim(event) {
    const id = Number(event.currentTarget.dataset.id);
    if (this.data.results.slice(0, this.data.revealed).some(row => row.claim_id === id)) go('/pages/reward-claim/index?id=' + id);
  },
  records() { go('/pages/points/index?tab=claims'); }
});
