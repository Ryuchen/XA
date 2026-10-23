const { config } = require('./config');
const tabs = require('./navigation').tabs.map(tab => tab.key);
// Same boundary as packages/money: 10 ledger units = 1 XA coin.
function money(raw) {
  const amount = Number(raw == null ? 0 : raw);
  if (!Number.isFinite(amount)) return '0';
  const coins = amount / 10;
  return coins.toFixed(Number.isInteger(coins) ? 0 : 1);
}
function date(value) {
  if (!value) return '';
  const text = String(value), legacy = text.replace('T', ' ').slice(0, 16);
  const iso = /^(\d{4}-\d{2}-\d{2})[Tt ](\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?([Zz]|([+-])(\d{2}):?(\d{2}))$/.exec(text);
  if (!iso) return legacy;
  const timestamp = Date.parse(text);
  if (!Number.isFinite(timestamp)) return legacy;
  const offset = iso[4] ? (iso[4] === '+' ? 1 : -1) * (Number(iso[5]) * 60 + Number(iso[6])) : 0;
  const format = instant => {
    const pad = number => String(number).padStart(2, '0');
    return String(instant.getUTCFullYear()).padStart(4, '0') + '-' + pad(instant.getUTCMonth() + 1) + '-' + pad(instant.getUTCDate()) + ' ' + pad(instant.getUTCHours()) + ':' + pad(instant.getUTCMinutes());
  };
  // Reject invalid dates normalized by Date.parse, while preserving old naive values.
  if (format(new Date(timestamp + offset * 60000)) !== iso[1] + ' ' + iso[2]) return legacy;
  return format(new Date(timestamp + 8 * 60 * 60000));
}
function toast(value) { wx.showToast({ title: (value && value.message) || String(value || '操作失败，请重试'), icon: 'none' }); }
function go(url) {
  const match = /^\/pages\/([a-z][a-z-]*)\/index(?:\?[^\r\n]*)?$/.exec(url || '');
  if (!match || !require('./routes').includes(match[1])) { toast('暂不支持此页面'); return; }
  const fail = error => {
    const reason = String(error && error.errMsg || '').slice(0, 300);
    console.warn('页面打开失败', reason);
    let diagnostic = false;
    try { diagnostic = wx.getAccountInfoSync().miniProgram.envVersion === 'develop'; } catch (_) {}
    wx.showModal({ title: '页面打开失败', content: diagnostic && reason ? reason : '请关闭小程序后重新进入，再试一次。', showCancel: false });
  };
  if (match[1] === 'catalog' || tabs.includes(match[1])) {
    // switchTab drops query parameters. Keep only validated business context.
    const params = {};
    (url.split('?')[1] || '').split('&').filter(Boolean).forEach(pair => {
      const [key, ...parts] = pair.split('=');
      try { params[key] = decodeURIComponent(parts.join('=')); } catch (_) {}
    });
    if (match[1] === 'catalog' && Object.keys(params).length) {
      const intent = {};
      if (/^[1-9]\d*$/.test(params.gameplay || '')) intent.gameplay = Number(params.gameplay);
      if (require('./catalog').serviceTabs.some(item => item.id === params.tab)) intent.tab = params.tab;
      if (params.keyword) intent.keyword = params.keyword.slice(0, 80);
      wx.setStorageSync('xa:native:catalog-intent', intent);
    }
    if (match[1] === 'orders') {
      const status = ['pending', 'grabbed', 'in_progress', 'completed', 'cancelled'].includes(params.status) ? params.status : '';
      wx.setStorageSync('xa:native:orders-intent', { status });
    }
    if (match[1] === 'catalog') wx.navigateTo({ url, fail });
    else wx.switchTab({ url: url.split('?')[0], fail });
  }
  else wx.navigateTo({ url, fail });
}
function image(url) {
  if (!url) return '';
  if (/^https?:\/\//.test(url)) return url;
  if (/^\/assets\//.test(url)) return url;
  if (!url.startsWith('/') || url.startsWith('//')) return '';
  try { return config().mediaBase + url; } catch (_) { return ''; }
}
function list(value) { return Array.isArray(value) ? value : ((value && (value.results || value.list)) || []); }
function richText(value) {
  return String(value || '').replace(/(<img\b[^>]*\bsrc=["'])(\/media\/club-catalog\/[^"']*)(["'])/gi,
    (_, before, path, after) => before + image(path) + after);
}
module.exports = { money, date, toast, go, image, list, richText };
