const events = require('./events');
const { runtime } = require('./runtime');
const KEY = 'xa:native:session:v1';
let session;
let revision = 0;
let navigating = false;
function getSession() {
  if (session === undefined) {
    try { session = wx.getStorageSync(KEY) || null; } catch (_) { session = null; }
    if (!session || !session.token || !session.user || !session.user.id || session.user.role !== 'customer') session = null;
  }
  return session;
}
function setSession(value) {
  const next = {
    token: value.token,
    refresh: value.refresh || value.refreshToken || '',
    user: value.user || value.userInfo,
  };
  if (!next.token || !next.user || !next.user.id || next.user.role !== 'customer') {
    throw new Error('该账号不是老板账号，请使用对应端登录');
  }
  wx.setStorageSync(KEY, next);
  session = next;
  revision += 1;
  events.emit('session.changed', next);
}
function updateToken(token) {
  const current = getSession();
  if (!current) return;
  session = { ...current, token };
  wx.setStorageSync(KEY, session);
  events.emit('token.changed', token);
}
function updateUser(patch) {
  const current = getSession();
  if (!current) return;
  const next = { ...current, user: { ...current.user, ...patch, id: current.user.id, role: current.user.role } };
  wx.setStorageSync(KEY, next);
  session = next;
  events.emit('profile.changed', next.user);
}
function clearSession() {
  session = null;
  revision += 1;
  try { wx.removeStorageSync(KEY); } catch (_) { /* in-memory state remains cleared */ }
  events.emit('session.changed', null);
}
function ensure() {
  if (getSession()) return true;
  if (!navigating) {
    navigating = true;
    wx.navigateTo({ url: '/pages/login/index', complete: () => { navigating = false; } });
  }
  return false;
}
function loginCode() {
  const current = runtime();
  if (current.platform === 'unknown' || !current.envVersion) return Promise.reject(new Error('无法确认登录环境，请重新进入后重试'));
  const app = current.platform === 'app';
  const getCode = app ? wx.getMiniProgramCode : wx.login;
  if (typeof getCode !== 'function' || (app && typeof wx.weixinMiniProgramLogin !== 'function')) {
    return Promise.reject(new Error(app
      ? '当前应用尚未配置小程序登录服务，请使用微信小程序登录或联系客服'
      : '微信登录暂不可用，请重新进入后重试'));
  }
  return new Promise((resolve, reject) => {
    let settled = false, collecting = false;
    const failed = error => {
      if (settled) return;
      settled = true;
      const cancelled = /cancel|取消/i.test(error && (error.errMsg || error.message) || '');
      reject(Object.assign(new Error(cancelled ? '已取消登录' : app
        ? '应用登录未完成，请稍后重试，或使用微信小程序登录'
        : '微信登录失败，请检查网络后重试'), { cancelled }));
    };
    const collect = () => {
      if (settled || collecting) return;
      collecting = true;
      try { getCode.call(wx, {
        timeout: 10000,
        success: result => {
          if (settled) return;
          settled = true;
          if (result && typeof result.code === 'string' && result.code.trim()) resolve(result.code);
          else reject(new Error('微信未返回登录凭证，请重试'));
        },
        fail: failed,
      }); } catch (error) { failed(error); }
    };
    // Official custom App login sequence: authorize first, then obtain a compatible code.
    // Ignore weixinMiniProgramLogin's identity-service credential (code2Verifyinfo);
    // only getMiniProgramCode's mini-program credential goes to existing code2session.
    if (app) {
      try { wx.weixinMiniProgramLogin({ success: () => collect(), fail: failed }); }
      catch (error) { failed(error); }
    } else collect();
  });
}

module.exports = { getSession, setSession, updateToken, updateUser, clearSession, ensure, loginCode, revision: () => revision };
