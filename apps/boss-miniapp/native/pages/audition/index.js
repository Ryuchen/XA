const auth = require('../../core/auth');
const { request } = require('../../core/api');
const { date, go, toast } = require('../../core/ui');
Page({
  data: { loading: true, error: '', info: null, busy: false, existingAccount: '' },
  onLoad(options) { this._alive = true; this._token = options.token || ''; this._role = options.role || ''; return this.load(); },
  onUnload() { this._alive = false; },
  onPullDownRefresh() { return this.load().finally(() => wx.stopPullDownRefresh()); },
  async load() {
    this.setData({ loading: true, error: '' });
    try {
      if (!this._token || this._role !== 'boss') throw new Error('此链接不是有效的老板试音邀请');
      const info = await request('/audition/info?token=' + encodeURIComponent(this._token) + '&role=boss', 'GET', undefined, { auth: false });
      const session = auth.getSession();
      if (this._alive) this.setData({ info: { ...info, expiry: info.expire_at ? date(info.expire_at) : '长期有效' }, existingAccount: session ? session.user.nickname : '' });
    } catch (error) { if (this._alive) this.setData({ error: error.message }); }
    finally { if (this._alive) this.setData({ loading: false }); }
  },
  async enter() {
    if (!this.data.info || this.data.busy) return;
    this.setData({ busy: true });
    try {
      const confirmation = await new Promise(resolve => wx.showModal({ title: '使用邀请绑定的账号', content: '该邀请将登录分享链接所绑定的老板账号。' + (auth.getSession() ? '这会切换当前登录账号。' : '') + '请仅使用客服发给你本人的邀请。', confirmText: '确认进入', success: resolve, fail: () => resolve({ confirm: false }) }));
      if (!confirmation.confirm || !this._alive) return;
      const result = await request('/audition/exchange', 'POST', { token: this._token, role: 'boss' }, { auth: false });
      if (this._alive) { auth.setSession(result); go('/pages/mine/index'); }
    } catch (error) { if (this._alive) toast(error); }
    finally { if (this._alive) this.setData({ busy: false }); }
  },
  support() { go('/pages/support/index'); },
});
