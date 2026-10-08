const auth = require('./auth');
const { config } = require('./config');
let refreshing = null;
const pendingFunds = new Map();
const cooldowns = new Map();
function failure(message, status, code, errorCode) {
  return Object.assign(new Error(message), { status, code, errorCode });
}
function raw(path, method, data, token) {
  return new Promise((resolve, reject) => {
    let base;
    try { base = config().apiBase; } catch (error) { reject(error); return; }
    wx.request({
      url: base + path, method, data, timeout: 15000,
      header: { 'Content-Type': 'application/json', ...(token ? { Authorization: 'Bearer ' + token } : {}) },
      success: resolve,
      fail: () => reject(failure('网络连接失败，请检查网络后重试', 0, 0)),
    });
  });
}
function unwrap(response) {
  const body = response.data;
  if (response.statusCode === 429) {
    const wait = Math.max(1, Number(body && body.retry_after) || 1);
    throw Object.assign(failure(`请求较多，请在 ${wait} 秒后重试`, 429, 429, body && body.errorCode), { retry_after: wait });
  }
  if (response.statusCode < 200 || response.statusCode >= 300 || !body || body.code !== 0) {
    throw Object.assign(failure((body && (body.msg || body.detail)) || '服务暂时不可用，请稍后重试', response.statusCode,
      body && body.code, body && body.errorCode), { fundResult: body && body.fundResult });
  }
  return body.data;
}
async function refresh() {
  const current = auth.getSession();
  const version = auth.revision();
  if (!current || !current.refresh) throw failure('登录已过期，请重新登录', 401, 401);
  if (!refreshing || refreshing.version !== version) {
    const operation = raw('/users/refresh/', 'POST', { refresh: current.refresh }, '').then(unwrap).then(data => {
      if (auth.revision() !== version) throw failure('账号已切换，请刷新页面', 409, 409);
      if (!data || !data.token) throw failure('登录已过期，请重新登录', 401, 401);
      auth.updateToken(data.token);
      return data.token;
    });
    refreshing = { version, operation };
    operation.finally(() => { if (refreshing && refreshing.operation === operation) refreshing = null; }).catch(() => {});
  }
  return refreshing.operation;
}
async function request(path, method = 'GET', data, options = {}) {
  if (!/^\/[a-zA-Z0-9]/.test(path)) throw new Error('无效的接口地址');
  const version = auth.revision();
  const current = options.auth === false ? null : auth.getSession();
  const scope = /^\/orders\/(create|quote)\//.exec(path);
  const cooldownKey = scope ? version + ':' + scope[1] : '';
  const wait = Math.ceil(((cooldowns.get(cooldownKey) || 0) - Date.now()) / 1000);
  if (wait > 0) throw Object.assign(failure(`请求较多，请在 ${wait} 秒后重试`, 429, 429), { retry_after: wait });
  let response = await raw(path, method, data, current && current.token);
  if (auth.revision() !== version) throw failure('账号已切换，请刷新页面', 409, 409);
  if (response.statusCode === 401 && current) {
    try {
      const latest = auth.getSession();
      const token = latest && latest.token !== current.token ? latest.token : await refresh();
      response = await raw(path, method, data, token);
      if (auth.revision() !== version) throw failure('账号已切换，请刷新页面', 409, 409);
      if (response.statusCode === 401) throw failure('登录已过期，请重新登录', 401, 401);
    } catch (error) {
      // A temporary outage is retryable; only a definitive auth failure ends the session.
      if (auth.revision() === version && (error.status === 401 || error.code === 401)) auth.clearSession();
      throw error;
    }
  }
  if (response.statusCode === 429 && cooldownKey) {
    cooldowns.set(cooldownKey, Date.now() + Math.min(300, Math.max(1, Number(response.data && response.data.retry_after) || 1)) * 1000);
  }
  return unwrap(response);
}
function canonical(value) {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (value && typeof value === 'object') return '{' + Object.keys(value).sort().map(k => JSON.stringify(k) + ':' + canonical(value[k])).join(',') + '}';
  return JSON.stringify(value);
}
function fund(path, payload) {
  const current = auth.getSession();
  if (!current) return Promise.reject(new Error('请先登录'));
  const key = 'xa:native:fund:' + current.user.id + ':' + path + ':' + canonical(payload);
  if (pendingFunds.has(key)) return pendingFunds.get(key);
  let id;
  try {
    id = wx.getStorageSync(key) || 'native-' + Date.now() + '-' + Math.random().toString(36).slice(2);
    wx.setStorageSync(key, id);
  } catch (_) { return Promise.reject(new Error('无法保存交易凭证，请检查设备存储后重试')); }
  const operation = request(path, 'POST', { ...payload, client_request_id: id }).catch(error => {
    if (error.code === 409 && error.errorCode === 'FUND_ALREADY_PROCESSED' && error.fundResult && error.fundResult.code === 0) {
      return error.fundResult.data;
    }
    throw error;
  }).then(result => {
    try { wx.removeStorageSync(key); } catch (_) { /* retain idempotency on storage failure */ }
    require('./events').emit('order.changed', result);
    return result;
  }).catch(error => {
    if (error.code >= 400 && error.code < 500 && error.code !== 409 && error.code !== 429 && error.status !== 0) {
      try { wx.removeStorageSync(key); } catch (_) { /* retain request ID */ }
    }
    throw error;
  }).finally(() => pendingFunds.delete(key));
  pendingFunds.set(key, operation);
  return operation;
}
async function upload(path, filePath, name = 'image', formData = {}) {
  const version = auth.revision();
  const send = () => new Promise((resolve, reject) => {
    const current = auth.getSession();
    wx.uploadFile({ url: config().apiBase + path, filePath, name, formData, timeout: 30000,
      header: current ? { Authorization: 'Bearer ' + current.token } : {},
      success: response => { try { resolve({ ...response, data: JSON.parse(response.data) }); } catch (_) { reject(new Error('上传返回异常，请重试')); } },
      fail: () => reject(new Error('图片上传失败，请检查网络后重试')) });
  });
  let response = await send();
  if (auth.revision() !== version) throw new Error('账号已切换，请重试');
  if (response.statusCode === 401) {
    try {
      await refresh();
      response = await send();
      if (auth.revision() !== version) throw new Error('账号已切换，请重试');
      if (response.statusCode === 401) throw failure('登录已过期，请重新登录', 401, 401);
    } catch (error) {
      if (auth.revision() === version && error.status === 401) auth.clearSession();
      throw error;
    }
  }
  if (auth.revision() !== version) throw new Error('账号已切换，请重试');
  return unwrap(response);
}
module.exports = { request, fund, upload };
