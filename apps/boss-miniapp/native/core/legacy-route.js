const mappings = {
  gameplay: 'catalog', category: 'catalog', productDetail: 'service',
  serviceCard: 'support', orderList: 'orders', messageDetail: 'message-detail', players: 'companions',
  'customer/wallet': 'wallet', 'customer/checkin': 'checkin', announcement: 'announcements',
  announcementDetail: 'announcement-detail', coupon: 'coupons', 'coupon/mine': 'coupons',
  favorite: 'favorites', settings: 'profile',
};
const number = value => /^[1-9]\d*$/.test(String(value || '')) ? String(value) : '';
function resolve(name, options) {
  let target = mappings[name];
  const params = [];
  const id = number(options.id || options.orderId);
  if (target === 'catalog') {
    wx.setStorageSync('xa:native:catalog-intent', {
      gameplay: name === 'gameplay' ? number(options.id) : number(options.gameplay),
      platform: ['MOBILE', 'PC'].includes(options.platform) ? options.platform : '',
      level: ['CONFIDENTIAL', 'TOP_SECRET'].includes(options.level) ? options.level : '',
    });
  } else if (name === 'orderList' && id) { target = 'order-detail'; params.push('id=' + id); }
  else if (target === 'companions') { const service = number(options.serviceId || options.id); if (service) { target = 'companion-picker'; params.push('service_id=' + service); } }
  else if (target === 'support') {
    if (number(options.orderId || options.order_id)) params.push('order_id=' + number(options.orderId || options.order_id));
    if (number(options.cardId || options.card_id)) params.push('card_id=' + number(options.cardId || options.card_id));
    if (options.scene === 'recharge') params.push('scene=recharge');
  } else if (id) params.push('id=' + id);
  if (name === 'coupon/mine') params.push('tab=mine');
  return '/pages/' + target + '/index' + (params.length ? '?' + params.join('&') : '');
}
module.exports = function legacyRoute(name) {
  return {
    onLoad(options) {
      const url = resolve(name, options);
      if (/^\/pages\/(companions|orders)\/index$/.test(url)) wx.switchTab({ url });
      else wx.redirectTo({ url });
    },
    home() { wx.switchTab({ url: '/pages/home/index' }); },
  };
};
