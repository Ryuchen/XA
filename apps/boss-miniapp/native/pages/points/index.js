const { request, fund } = require('../../core/api');
const auth = require('../../core/auth');
const { on } = require('../../core/events');
const { go, image, toast, date } = require('../../core/ui');
Page({
  data: { logged: false, loading: false, busy: false, error: '', points: 0, tab: 'shop', kind: '', rewards: [], visible: [], ledger: [], claims: [], next: null,
    kook: null, bindingCode: '',
    kinds: [{ id: '', name: '全部' }, { id: 'PHYSICAL', name: '实物礼物' }, { id: 'VIRTUAL', name: '虚拟物品' }, { id: 'SERVICE', name: '专属权益' }, { id: 'BALANCE', name: '余额兑换' }] },
  onLoad(options = {}) { if (['shop', 'claims', 'ledger'].includes(options.tab)) this.setData({ tab: options.tab }); this._alive = true; this._seq = 0; this._off = on('session.changed', () => { this._seq++; this.setData({ points: 0, rewards: [], visible: [], ledger: [], claims: [], kook: null, bindingCode: '', busy: false }); if (this._visible) this.load(); }); },
  onShow() { this._visible = true; this.load(); }, onHide() { this._visible = false; },
  onUnload() { this._alive = false; this._off(); },
  async onPullDownRefresh() { await this.load(); wx.stopPullDownRefresh(); },
  async load() {
    const seq = ++this._seq, logged = !!auth.getSession(); this.setData({ logged, loading: logged, error: '' }); if (!logged) return;
    try {
      const [points, rewards, claims, kook] = await Promise.all([request('/club/points/'), request('/club/rewards/'), request('/club/claims/'), request('/club/kook-binding/')]);
      if (!this._alive || seq !== this._seq) return;
      this.setData({ kook, points: points.points, ledger: points.list.map(row => ({ ...row, time: date(row.created_at) })), next: points.next,
        rewards: rewards.map(row => ({ ...row, cover: image(row.image_url), icon: ({ PHYSICAL: 'headphones', VIRTUAL: 'trophy', SERVICE: 'ticket', NAMING: 'award', TAG: 'award', BALANCE: 'ticket' })[row.kind] || 'ticket', soldOut: row.stock === 0 })),
        claims: claims.map(row => ({ ...row, time: date(row.created_at) })), loading: false }); this.filter();
    } catch (e) { if (this._alive && seq === this._seq) this.setData({ error: e.message, loading: false }); }
  },
  tab(event) { this.setData({ tab: event.currentTarget.dataset.id }); },
  async bindKook() { if(this.data.busy)return;const version=auth.revision();this.setData({busy:true});try{const result=await request('/club/kook-binding/','POST',{});if(this._alive&&version===auth.revision()){this.setData({bindingCode:result.code});wx.setClipboardData({data:'绑定 '+result.code});}}catch(e){if(this._alive&&version===auth.revision())toast(e);}finally{if(this._alive&&version===auth.revision())this.setData({busy:false});} },
  kind(event) { this.setData({ kind: event.currentTarget.dataset.id }); this.filter(); },
  filter() { this.setData({ visible: this.data.rewards.filter(row => !this.data.kind || row.kind === this.data.kind || (this.data.kind === 'SERVICE' && ['TAG', 'NAMING'].includes(row.kind))) }); },
  async redeem(event) {
    if (!auth.ensure() || this.data.busy) return;
    const reward = this.data.rewards.find(row => row.id === Number(event.currentTarget.dataset.id));
    if (!reward || reward.soldOut) return;
    if (this.data.points < reward.point_cost) return toast('积分还不够，继续累积吧');
    const version = auth.revision(); this.setData({ busy: true });
    try {
      const answer = await new Promise(resolve => wx.showModal({ title: '确认兑换', content: '使用 ' + reward.point_cost + ' 积分兑换「' + reward.name + '」。' + (reward.kind === 'BALANCE' ? '兑换成功后余额将自动到账。' : '兑换后可在记录中补充交付资料。'), success: resolve, fail: () => resolve({ confirm: false }) }));
      if (!answer.confirm || !this._alive || version !== auth.revision()) return;
      const claim = await fund('/club/redeem/', { reward_id: reward.id });
      if (!this._alive || version !== auth.revision()) return;
      go('/pages/reward-claim/index?id=' + claim.id);
    } catch (e) { if (this._alive && version === auth.revision()) toast(e); }
    finally { if (this._alive && version === auth.revision()) this.setData({ busy: false }); }
  },
  claim(event) { go('/pages/reward-claim/index?id=' + event.currentTarget.dataset.id); },
  async more() { if (!this.data.next || this.data.busy) return; const seq = this._seq; this.setData({ busy: true }); try { const data = await request('/club/points/?before=' + this.data.next); if (this._alive && seq === this._seq) this.setData({ ledger: [...this.data.ledger, ...data.list.map(row => ({ ...row, time: date(row.created_at) }))], next: data.next }); } catch (e) { toast(e); } finally { if (this._alive && seq === this._seq) this.setData({ busy: false }); } },
  lottery() { go('/pages/lottery/index'); }, rules() { go('/pages/club-content/index?kind=POINT_RULES'); }, login() { auth.ensure(); }
});
