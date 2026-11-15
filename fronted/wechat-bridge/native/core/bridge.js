const environments = require('../config');
const paths = new Set([
  '/users/web-login/authorize/',
  '/users/web-login/sms/',
  '/users/web-login/confirm/',
]);

function apiBase() {
  const info = wx.getAccountInfoSync();
  const env = info && info.miniProgram && info.miniProgram.envVersion;
  const base = String(environments[env] && environments[env].apiBase || '').replace(/\/$/, '');
  if (!base || (env !== 'develop' && !/^https:\/\//.test(base))) {
    throw new Error('微信授权服务地址尚未配置，请联系平台客服');
  }
  return base;
}

function loginCode() {
  return new Promise((resolve, reject) => {
    wx.login({
      success: result => result.code ? resolve(result.code) : reject(new Error('微信登录未完成，请重试')),
      fail: () => reject(new Error('微信登录失败，请检查网络后重试')),
    });
  });
}

function request(path, method, data) {
  if (!paths.has(path) || method !== 'POST') return Promise.reject(new Error('无效的微信授权操作'));
  return new Promise((resolve, reject) => {
    let base;
    try { base = apiBase(); } catch (error) { reject(error); return; }
    wx.request({
      url: base + path, method: 'POST', data, timeout: 15000,
      header: { 'Content-Type': 'application/json' },
      success: response => {
        const body = response.data;
        if (response.statusCode >= 200 && response.statusCode < 300 && body && body.code === 0) {
          resolve(body.data);
        } else {
          reject(Object.assign(new Error(body && (body.msg || body.detail) || '授权服务暂不可用，请重试'), {
            status: response.statusCode,
            retry_after: body && body.retry_after,
          }));
        }
      },
      fail: () => reject(new Error('网络连接失败，请检查网络后重试')),
    });
  });
}

module.exports = { loginCode, request };
