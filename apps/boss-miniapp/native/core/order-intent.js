// Keep the approved payload and request ID together until its result is known.
// A different checkout page or dispatch route must not create a second payment.
const PATH = '/orders/create/';
const keyFor = id => 'xa:native:order-intent:v1:' + id;
const legacyPrefix = id => 'xa:native:fund:' + id + ':' + PATH + ':';
const idValid = value => typeof value === 'string' && /^[a-zA-Z0-9_-]{8,64}$/.test(value);
function canonical(value) {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (value && typeof value === 'object') return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}';
  return JSON.stringify(value);
}
function unavailable() { return new Error('无法读取待核对订单凭证，请检查设备存储后重试'); }
function valid(record) {
  return record && idValid(record.requestId) && record.payload && typeof record.payload === 'object' && !Array.isArray(record.payload);
}
function load(accountId) {
  try {
    const stored = wx.getStorageSync(keyFor(accountId));
    if (stored) return valid(stored) ? stored : { blocked: true };
    const prefix = legacyPrefix(accountId);
    const keys = wx.getStorageInfoSync().keys.filter(key => key.startsWith(prefix));
    if (!keys.length) return null;
    const records = keys.map(key => {
      const tail = key.slice(prefix.length).replace(/:request:[a-zA-Z0-9_-]{8,64}$/, '');
      const payload = JSON.parse(tail), requestId = wx.getStorageSync(key);
      return { payload, requestId, attempted: true, legacyKeys: [key] };
    });
    if (records.some(record => !valid(record))) return { blocked: true };
    const first = records[0];
    // Process historical requests one at a time. Only keys representing the
    // same request may be removed together after that request is resolved.
    first.legacyKeys = records.filter(record => record.requestId === first.requestId && JSON.stringify(record.payload) === JSON.stringify(first.payload)).map(record => record.legacyKeys[0]);
    wx.setStorageSync(keyFor(accountId), first);
    return first;
  } catch (_) { throw unavailable(); }
}
function begin(accountId, record) {
  if (load(accountId)) return false;
  try { wx.setStorageSync(keyFor(accountId), record); return true; } catch (_) { throw unavailable(); }
}
function update(accountId, record) {
  try {
    const current = wx.getStorageSync(keyFor(accountId));
    if (!valid(current) || current.requestId !== record.requestId || canonical(current.payload) !== canonical(record.payload)) return false;
    wx.setStorageSync(keyFor(accountId), record);
    return true;
  } catch (_) { throw unavailable(); }
}
function clear(accountId, record) {
  try {
    const current = wx.getStorageSync(keyFor(accountId));
    if (!valid(current) || current.requestId !== record.requestId || canonical(current.payload) !== canonical(record.payload)) return false;
    const prefix = legacyPrefix(accountId);
    const keys = wx.getStorageInfoSync().keys.filter(key => {
      if (!key.startsWith(prefix) || wx.getStorageSync(key) !== record.requestId) return false;
      try { return canonical(JSON.parse(key.slice(prefix.length).replace(/:request:[a-zA-Z0-9_-]{8,64}$/, ''))) === canonical(record.payload); }
      catch (_) { return false; }
    });
    for (const key of keys) wx.removeStorageSync(key);
    wx.removeStorageSync(keyFor(accountId));
    return true;
  } catch (_) { throw unavailable(); }
}
function create(payload) {
  return { payload, requestId: 'native-' + Date.now() + '-' + Math.random().toString(36).slice(2), attempted: false, legacyKeys: [] };
}
module.exports = { load, begin, update, clear, create };
