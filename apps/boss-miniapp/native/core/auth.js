const events = require('./events');
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
  return new Promise((resolve, reject) => wx.login({
    timeout: 10000,
    success: result => result.code ? resolve(result.code) : reject(new Error('微信未返回登录凭证，请重试')),
    fail: () => reject(new Error('微信登录失败，请检查网络后重试')),
  }));
}
module.exports = { getSession, setSession, updateToken, updateUser, clearSession, ensure, loginCode, revision: () => revision };
