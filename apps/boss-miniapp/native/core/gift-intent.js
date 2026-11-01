// Retain an approved gift request until its outcome is known, even after leaving the page.
const keyFor = id => 'xa:native:gift-intent:v1:' + id;
const fundPrefix = id => 'xa:native:fund:' + id + ':';
const validPath = path => path === '/orders/gifts/send/' || /^\/orders\/orders\/\d+\/tip\/$/.test(path);
const valid = r => r && validPath(r.path) && /^[a-zA-Z0-9_-]{8,64}$/.test(r.requestId || '') && r.payload && typeof r.payload === 'object' && !Array.isArray(r.payload);
const canonical = v => Array.isArray(v) ? '[' + v.map(canonical).join(',') + ']' : v && typeof v === 'object' ? '{' + Object.keys(v).sort().map(k => JSON.stringify(k) + ':' + canonical(v[k])).join(',') + '}' : JSON.stringify(v);
function unavailable() { return new Error('无法读取待核对礼物凭证，请检查设备存储'); }
function load(id) {
  try {
    const current = wx.getStorageSync(keyFor(id));
    if (current) return valid(current) ? current : { blocked: true };
    const prefix = fundPrefix(id);
    for (const key of wx.getStorageInfoSync().keys.filter(k => k.startsWith(prefix))) {
      const tail = key.slice(prefix.length), match = tail.match(/^(\/orders\/gifts\/send\/|\/orders\/orders\/\d+\/tip\/):(.*)$/);
      if (!match) continue;
      const record = { path: match[1], payload: JSON.parse(match[2].replace(/:request:[a-zA-Z0-9_-]{8,64}$/, '')), requestId: wx.getStorageSync(key), attempted: true };
      if (!valid(record)) return { blocked: true };
      wx.setStorageSync(keyFor(id), record);
      return record;
    }
    return null;
  } catch (_) { throw unavailable(); }
}
function begin(id, record) {
  if (load(id)) return false;
  try { wx.setStorageSync(keyFor(id), record); return true; } catch (_) { throw unavailable(); }
}
function same(a, b) { return valid(a) && a.path === b.path && a.requestId === b.requestId && canonical(a.payload) === canonical(b.payload); }
function update(id, record) {
  try { if (!same(wx.getStorageSync(keyFor(id)), record)) return false; wx.setStorageSync(keyFor(id), record); return true; } catch (_) { throw unavailable(); }
}
function clear(id, record) {
  try {
    if (!same(wx.getStorageSync(keyFor(id)), record)) return false;
    const prefix = fundPrefix(id) + record.path + ':';
    for (const key of wx.getStorageInfoSync().keys.filter(k => k.startsWith(prefix))) {
      if (wx.getStorageSync(key) !== record.requestId) continue;
      try { if (canonical(JSON.parse(key.slice(prefix.length).replace(/:request:[a-zA-Z0-9_-]{8,64}$/, ''))) === canonical(record.payload)) wx.removeStorageSync(key); } catch (_) { /* retain unrelated keys */ }
    }
    wx.removeStorageSync(keyFor(id));
    return true;
  } catch (_) { throw unavailable(); }
}
function create(path, payload, display) { return { path, payload, display, requestId: 'native-' + Date.now() + '-' + Math.random().toString(36).slice(2), attempted: false }; }
module.exports = { load, begin, update, clear, create };
