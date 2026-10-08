const { money, date } = require('./ui');
const statuses = { PENDING: '待接单', GRABBED: '已接单', IN_SERVICE: '服务中', COMPLETED: '已完成', CANCELLED: '已取消' };
function present(order) {
  const allowed = order.allowed_actions || [];
  const isGift = !!order.service?.is_gift;
  return { ...order, isGift, service: { ...order.service, name: order.service_name_snapshot || order.service?.name }, statusText: isGift && order.status === 'COMPLETED' ? '礼物已送达' : statuses[order.status] || '状态更新中', amountText: money(order.amount), originalText: money(order.original_amount), createdText: date(order.created_at),
    canSelectProvider: allowed.includes('SELECT_PROVIDER'), canCancel: allowed.includes('CANCEL'), canEvaluate: allowed.includes('EVALUATE'), canTip: allowed.includes('TIP'),
    timeline: isGift ? [{ title: '礼物已送达', time: date(order.completed_at || order.created_at) }] : [
      { title: '下单成功', time: date(order.created_at) },
      { title: '陪玩已接单', time: date(order.grabbed_at) },
      { title: '服务已开始', time: date(order.in_service_at) },
      { title: '服务已完成', time: date(order.completed_at) },
      { title: '订单已取消', time: date(order.cancelled_at) },
      { title: '退款已入账', time: date(order.refunded_at) },
    ].filter(item => item.time) };
}
module.exports = { present, statuses };
