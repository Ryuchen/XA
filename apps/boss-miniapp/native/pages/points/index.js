const { modal: themedModal } = require('../../core/ui');
const { request } = require('../../core/api');
const auth = require('../../core/auth');
const { on } = require('../../core/events');
const { go, image, toast, date } = require('../../core/ui');
Page({
  data: { logged: false, loading: false, busy: false, error: '', points: 0, tab: 'shop', kind: '', rewards: [], visible: [], ledger: [], claims: [], claimsNext: null, claimsBusy: false, next: null,
    kook: null, bindingCode: '', pendingRedemption: null,
    kinds: [{ id: '', name: '全部' }, { id: 'PHYSICAL', name: '实物礼物' }, { id: 'VIRTUAL', name: '虚拟物品' }, { id: 'SERVICE', name: '专属权益' }, { id: 'BALANCE', name: '余额兑换' }] },
  onLoad(options = {}) { if (['shop', 'claims', 'ledger'].includes(options.tab)) this.setData({ tab: options.tab }); this._alive = true; this._seq = 0; this._off = on('session.changed', () => { this._seq++; this.setData({ points: 0, rewards: [], visible: [], ledger: [], claims: [], claimsNext: null, claimsBusy: false, kook: null, bindingCode: '', busy: false }); this.restoreRedemption(); if (this._visible) this.load(); }); },
  onShow() { this._visible = true; this.restoreRedemption(); this.load(); }, onHide() { this._visible = false; },
  onUnload() { this._alive = false; this._off(); },
  async onPullDownRefresh() { await this.load(); wx.stopPullDownRefresh(); },
  async load() {
    const seq = ++this._seq, logged = !!auth.getSession(); this.setData({ logged, loading: logged, claimsBusy: false, error: '' }); if (!logged) return;
    try {
      const [points, rewards, claims, kook] = await Promise.all([request('/club/points/'), request('/club/rewards/'), request('/club/claims/?page_size=20'), request('/club/kook-binding/')]);
      if (!this._alive || seq !== this._seq) return;
      this.setData({ kook, points: points.points, ledger: points.list.map(row => ({ ...row, time: date(row.created_at) })), next: points.next,
        rewards: rewards.map(row => ({ ...row, cover: image(row.image_url), icon: ({ PHYSICAL: 'headphones', VIRTUAL: 'trophy', SERVICE: 'ticket', NAMING: 'award', TAG: 'award', BALANCE: 'ticket' })[row.kind] || 'ticket', soldOut: row.stock === 0 })),
        claims: claims.items.map(row => ({ ...row, time: date(row.created_at) })), claimsNext: claims.next, claimsBusy: false, loading: false }); this.filter();
    } catch (e) { if (this._alive && seq === this._seq) this.setData({ error: e.message, loading: false }); }
  },
  tab(event) { this.setData({ tab: event.currentTarget.dataset.id }); },
  async bindKook() { if(this.data.busy)return;const version=auth.revision();this.setData({busy:true});try{const result=await request('/club/kook-binding/','POST',{});if(this._alive&&version===auth.revision()){this.setData({bindingCode:result.code});wx.setClipboardData({data:'绑定 '+result.code});}}catch(e){if(this._alive&&version===auth.revision())toast(e);}finally{if(this._alive&&version===auth.revision())this.setData({busy:false});} },
  kind(event) { this.setData({ kind: event.currentTarget.dataset.id }); this.filter(); },
  filter() { this.setData({ visible: this.data.rewards.filter(row => !this.data.kind || row.kind === this.data.kind || (this.data.kind === 'SERVICE' && ['TAG', 'NAMING'].includes(row.kind))) }); },
  redemptionKey() { const current = auth.getSession(); return current ? 'xa:native:points-redemption:' + current.user.id : ''; },
  restoreRedemption() { const key = this.redemptionKey(); try { this.setData({ pendingRedemption: key ? wx.getStorageSync(key) || null : null }); } catch (_) { this.setData({ pendingRedemption: null }); } },
  async submitRedemption(pending, version) {
    const key = this.redemptionKey();
    try {
      const claim = await request('/club/redeem/', 'POST', { reward_id: pending.rewardId, client_request_id: pending.requestId });
      if (!this._alive || version !== auth.revision()) return;
      wx.removeStorageSync(key); this.setData({ pendingRedemption: null });
      go('/pages/reward-claim/index?id=' + claim.id);
    } catch (e) {
      if (!this._alive || version !== auth.revision()) return;
      if (e.status >= 400 && e.status < 500 && e.status !== 409 && e.status !== 429) {
        try { wx.removeStorageSync(key); this.setData({ pendingRedemption: null }); } catch (_) { /* retain the original request */ }
        toast(e); return;
      }
      toast('兑换结果未确认，请重试原申请');
    }
  },
  async retryRedemption() { if (this.data.busy || !this.data.pendingRedemption || !auth.ensure()) return; const version = auth.revision(); this.setData({ busy: true }); try { await this.submitRedemption(this.data.pendingRedemption, version); } finally { if (this._alive && version === auth.revision()) this.setData({ busy: false }); } },
  async redeem(event) {
    if (!auth.ensure() || this.data.busy || this.data.pendingRedemption) return;
    const reward = this.data.rewards.find(row => row.id === Number(event.currentTarget.dataset.id));
    if (!reward || reward.soldOut) return;
    if (this.data.points < reward.point_cost) return toast('积分还不够，继续累积吧');
    const version = auth.revision(); this.setData({ busy: true });
    try {
      const answer = await new Promise(resolve => themedModal({ title: '确认兑换', content: '使用 ' + reward.point_cost + ' 积分兑换「' + reward.name + '」。' + (reward.kind === 'BALANCE' ? '兑换成功后余额将自动到账。' : '兑换后可在记录中补充交付资料。'), success: resolve, fail: () => resolve({ confirm: false }) }));
      if (!answer.confirm || !this._alive || version !== auth.revision()) return;
      const pending = { rewardId: reward.id, name: reward.name, requestId: 'redeem-' + Date.now() + '-' + Math.random().toString(36).slice(2) };
      wx.setStorageSync(this.redemptionKey(), pending); this.setData({ pendingRedemption: pending });
      await this.submitRedemption(pending, version);
    } catch (e) { if (this._alive && version === auth.revision()) toast(e); }
    finally { if (this._alive && version === auth.revision()) this.setData({ busy: false }); }
  },
  claim(event) { go('/pages/reward-claim/index?id=' + event.currentTarget.dataset.id); },
  async moreClaims() { if (!this.data.claimsNext || this.data.claimsBusy || this.data.loading) return; const seq = this._seq; this.setData({ claimsBusy: true }); try { const data = await request('/club/claims/?page_size=20&before=' + this.data.claimsNext); if (this._alive && seq === this._seq) { const known = new Set(this.data.claims.map(row => row.id)); this.setData({ claims: [...this.data.claims, ...data.items.filter(row => !known.has(row.id)).map(row => ({ ...row, time: date(row.created_at) }))], claimsNext: data.next }); } } catch (e) { if (this._alive && seq === this._seq) toast(e); } finally { if (this._alive && seq === this._seq) this.setData({ claimsBusy: false }); } },
  async more() { if (!this.data.next || this.data.busy) return; const seq = this._seq; this.setData({ busy: true }); try { const data = await request('/club/points/?before=' + this.data.next); if (this._alive && seq === this._seq) this.setData({ ledger: [...this.data.ledger, ...data.list.map(row => ({ ...row, time: date(row.created_at) }))], next: data.next }); } catch (e) { toast(e); } finally { if (this._alive && seq === this._seq) this.setData({ busy: false }); } },
  lottery() { go('/pages/lottery/index'); }, rules() { go('/pages/club-content/index?kind=POINT_RULES'); }, login() { auth.ensure(); }
});
