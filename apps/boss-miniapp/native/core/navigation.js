const tabs = [
  { key: 'home', text: '首页' },
  { key: 'companions', text: '陪玩' },
  { key: 'orders', text: '订单' },
  { key: 'messages', text: '消息' },
  { key: 'mine', text: '我的' },
];
const sections = {
  companions: ['companions', 'companion-picker', 'companion-detail', 'players'],
  orders: ['orders', 'order-detail', 'orderList'],
  messages: ['messages', 'message-detail', 'messageDetail', 'announcements', 'announcement', 'announcement-detail', 'announcementDetail', 'support', 'serviceCard', 'chat'],
  mine: ['mine', 'login', 'wallet', 'customer/wallet', 'profile', 'security', 'settings', 'achievements', 'rankings', 'membership', 'coupons', 'coupon', 'coupon/mine', 'favorites', 'favorite', 'checkin', 'customer/checkin', 'points', 'reward-claim', 'lottery', 'audition'],
};
function selectedTab(route) {
  const name = String(route || '').replace(/^\/?pages\//, '').replace(/\/index(?:\?.*)?$/, '');
  return Object.keys(sections).find(key => sections[key].includes(name)) || 'home';
}
module.exports = { tabs, selectedTab };
