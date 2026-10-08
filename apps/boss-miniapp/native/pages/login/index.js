const auth = require('../../core/auth');
const { request } = require('../../core/api');
const { go, toast } = require('../../core/ui');
const { brandAssets } = require('../../core/brand-assets');
Page({
  data: { ...brandAssets(), agreed: false, busy: false, requiresBinding: false, error: '' },
  onLoad() { this._alive = true; },
  onUnload() { this._alive = false; },
  agree(event) { this.setData({ agreed: event.detail.value.includes('agree') }); },
  privacy() {
    if (wx.openPrivacyContract) wx.openPrivacyContract({ fail: () => toast('暂未配置隐私保护指引，请联系客服') });
    else toast('请升级微信后查看隐私保护指引');
  },
  async login(phoneCode, create) {
    if (!this.data.agreed) { toast('请先阅读并同意隐私保护指引'); return; }
    if (this.data.busy) return;
    this.setData({ busy: true, error: '' });
    try {
      const code = await auth.loginCode();
      const result = await request('/users/wechat-login/', 'POST', { code, ...(phoneCode ? { phoneCode } : {}), ...(!phoneCode && !create ? { checkOnly: true } : {}) }, { auth: false });
      if (!this._alive) return;
      if (result.requiresBinding) { this.setData({ requiresBinding: true }); return; }
      auth.setSession(result);
      wx.navigateBack({ fail: () => wx.switchTab({ url: '/pages/mine/index' }) });
    } catch (error) { if (this._alive) this.setData({ error: error.message }); }
    finally { if (this._alive) this.setData({ busy: false }); }
  },
  quickLogin() { return this.login(); },
  phone(event) {
    if (!event.detail.code) { this.setData({ error: '未获得手机号授权，你仍可选择创建新账号或联系客服' }); return; }
    return this.login(event.detail.code);
  },
  createAccount() {
    if (this.data.busy || !this.data.agreed) return;
    wx.showModal({ title: '创建新的老板账号', content: '新账号拥有独立的钱包和订单。已有余额或历史订单，请优先授权手机号找回原账号。', confirmText: '确认创建', success: result => { if (result.confirm && this._alive) this.login('', true); } });
  },
  support() { go('/pages/support/index'); },
});
