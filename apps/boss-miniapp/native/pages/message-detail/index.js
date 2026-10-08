const api = require('../../core/api');
const auth = require('../../core/auth');
const ui = require('../../core/ui');
const events = require('../../core/events');

// Historical messages retain Taro route names. Resolve only known internal destinations.
function destination(message) {
  if (/^[1-9]\d*$/.test(String(message.related_order_id || ''))) return '/pages/order-detail/index?id=' + message.related_order_id;
  const source = String(message.action_url || '');
  if (!/^\/pages\/[a-zA-Z0-9/-]+(?:\?[^#]*)?$/.test(source)) return '';
  const parts = source.split('?');
  const params = {};
  (parts[1] || '').split('&').forEach(pair => {
    const split = pair.split('=');
    try { params[split[0]] = decodeURIComponent(split.slice(1).join('=')); } catch (_) {}
  });
  const orderId = params.orderId || params.id;
  if (parts[0] === '/pages/orderList/index' && /^[1-9]\d*$/.test(orderId || '')) return '/pages/order-detail/index?id=' + orderId;
  const routes = {
    '/pages/orderList/index': '/pages/orders/index', '/pages/customer/checkin/index': '/pages/checkin/index',
    '/pages/customer/wallet/index': '/pages/wallet/index', '/pages/coupon/mine/index': '/pages/coupons/index?tab=mine',
    '/pages/coupon/index': '/pages/coupons/index', '/pages/serviceCard/index': '/pages/support/index',
    '/pages/chat/index': '/pages/messages/index', '/pages/announcement/index': '/pages/announcements/index',
    '/pages/announcementDetail/index': '/pages/announcement-detail/index', '/pages/productDetail/index': '/pages/service/index',
    '/pages/category/index': '/pages/catalog/index', '/pages/companions/index': '/pages/catalog/index',
    '/pages/favorite/index': '/pages/favorites/index'
  };
  if (routes[parts[0]]) {
    const target = routes[parts[0]];
    return target + (!target.includes('?') && /^[1-9]\d*$/.test(params.id || '') ? '?id=' + params.id : '');
  }
  const allowed = ['home','catalog','orders','mine','messages','wallet','checkin','coupons','support','favorites','announcements','announcement-detail','service','order-detail'];
  const page = parts[0].replace(/^\/pages\//, '').replace(/\/index$/, '');
  if (!allowed.includes(page)) return '';
  return parts[0] + (/^[1-9]\d*$/.test(params.id || '') ? '?id=' + params.id : '');
}

const { privatePage } = require('../../core/page');
Page(privatePage({
  data: { loading: false, error: '', item: null, target: '' },
  init(options) { this._id = options.id; },
  async fetch() {
    if (!/^[1-9]\d*$/.test(this._id || '')) throw new Error('消息链接无效');
      const item = await api.request('/messages/' + this._id + '/');
      events.emit('message.changed');
      return { item: Object.assign({}, item, { dateText: ui.date(item.created_at) }), target: destination(item) };
  },
  action() { if (this.data.target) ui.go(this.data.target); }
}));
