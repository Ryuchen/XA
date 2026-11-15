const { loginCode, request } = require('../../core/bridge');

Page({
  data: { agreed: false, busy: false, authorized: false, storeName: '',
    requiresPhoneAuthorization: false, requiresSms: false, canConfirm: false,
    maskedPhone: '', smsCode: '', countdown: 0, error: '', confirmed: false },
  onLoad(options) {
    this._alive = true;
    let ticket;
    try { ticket = String(options.ticket || decodeURIComponent(options.scene || '')).trim(); }
    catch (_) { this.setData({ error: '登录请求无效，请返回网页刷新二维码' }); return; }
    if (!/^[0-9a-f]{32}$/i.test(ticket) && !/^[0-9a-z]{1,9}_[A-Za-z0-9_-]{22}$/.test(ticket)) { this.setData({ error: '登录请求无效，请返回网页刷新二维码' }); return; }
    this._ticket = ticket;
  },
  onUnload() { this._alive = false; this._bridgeToken = ''; this._ticket = ''; if (this._timer) clearInterval(this._timer); },
  agree(event) { this.setData({ agreed: event.detail.value.includes('agree') }); },
  codeInput(event) { this.setData({ smsCode: event.detail.value.replace(/\D/g, '').slice(0, 6) }); },
  privacy() {
    if (wx.openPrivacyContract) wx.openPrivacyContract({ fail: () => this.setData({ error: '隐私指引暂不可用，请联系平台客服' }) });
  },
  async authorize(phoneCode) {
    if (!this.data.agreed || !this._ticket || this.data.busy) return;
    this.setData({ busy: true, error: '' });
    try {
      const code = await loginCode();
      const data = await request('/users/web-login/authorize/', 'POST', { ticket: this._ticket, code, ...(phoneCode ? { phoneCode } : {}) }, {});
      if (!this._alive) return;
      this._bridgeToken = data.bridge_token;
      this.setData({ storeName: data.store && data.store.name || '', authorized: true, requiresPhoneAuthorization: !!data.requiresPhoneAuthorization, requiresSms: !!data.requiresSms, canConfirm: !!data.canConfirm, maskedPhone: data.maskedPhone || '', smsCode: '' });
    } catch (error) { if (this._alive) this.setData({ error: error.message }); }
    finally { if (this._alive) this.setData({ busy: false }); }
  },
  quickLogin() { return this.authorize(); },
  phone(event) {
    if (!event.detail.code) { this.setData({ error: '未获得微信手机号授权，请授权后继续' }); return; }
    return this.authorize(event.detail.code);
  },
  async sendCode() {
    if (!this.data.agreed || this.data.busy || this.data.countdown || !this._bridgeToken) return;
    this.setData({ busy: true, error: '' });
    try {
      const result = await request('/users/web-login/sms/', 'POST', { ticket: this._ticket, bridge_token: this._bridgeToken }, {});
      if (!this._alive) return;
      this.setData({ countdown: result.retry_after || 60 });
      this._timer = setInterval(() => {
        if (!this._alive) return;
        const seconds = Math.max(0, this.data.countdown - 1);
        this.setData({ countdown: seconds });
        if (!seconds) clearInterval(this._timer);
      }, 1000);
    } catch (error) { if (this._alive) this.setData({ error: error.message }); }
    finally { if (this._alive) this.setData({ busy: false }); }
  },
  async confirm() {
    if (!this.data.agreed || this.data.busy || !this._bridgeToken || (this.data.requiresSms && this.data.smsCode.length !== 6)) return;
    this.setData({ busy: true, error: '' });
    try {
      await request('/users/web-login/confirm/', 'POST', { ticket: this._ticket, bridge_token: this._bridgeToken, smsCode: this.data.smsCode }, {});
      if (this._alive) this.setData({ confirmed: true });
    } catch (error) { if (this._alive) this.setData({ error: error.message }); }
    finally { if (this._alive) this.setData({ busy: false }); }
  },
});
