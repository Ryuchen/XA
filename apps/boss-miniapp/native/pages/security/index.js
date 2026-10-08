const auth = require('../../core/auth');
const { request } = require('../../core/api');
const { privatePage } = require('../../core/page');
const { toast, go } = require('../../core/ui');
Page(privatePage({
  data: { profile: null, busy: false, actionError: '' },
  async fetch() { return { profile: await request('/users/me/') }; },
  async bind(phoneCode) {
    if (!auth.ensure() || this.data.busy) return;
    this.setData({ busy: true, actionError: '' });
    try {
      const code = await auth.loginCode();
      const result = await request('/users/wechat-bind/', 'POST', { code, ...(phoneCode ? { phoneCode } : {}) });
      const session = auth.getSession();
      if (!this._alive || !session) return;
      auth.setSession({ ...session, user: { ...session.user, ...result.userInfo } });
      toast('绑定信息已更新');
    } catch (error) { if (this._alive) this.setData({ actionError: error.message }); }
    finally { if (this._alive) this.setData({ busy: false }); }
  },
  bindWechat() { this.bind(); },
  phone(event) { if (event.detail.code) this.bind(event.detail.code); else toast('未授权手机号，绑定信息保持不变'); },
  support() { go('/pages/support/index'); },
  logout() { wx.showModal({ title: '退出登录', content: '退出后可使用微信重新登录，钱包和订单仍会保留。', success: result => { if (result.confirm) { auth.clearSession(); wx.switchTab({ url: '/pages/mine/index' }); } } }); },
  privacy() { if (wx.openPrivacyContract) wx.openPrivacyContract({ fail: () => toast('隐私保护指引暂未配置') }); },
}));
