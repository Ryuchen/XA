const { money, date } = require('./ui');
const statuses = { PENDING: '待接单', GRABBED: '已接单', IN_SERVICE: '服务中', COMPLETED: '已完成', CANCELLED: '已取消' };
function present(order) {
  const allowed = order.allowed_actions || [];
  const isGift = !!(order.source_order || (order.is_gift_snapshot ?? order.service?.is_gift));
  // UNPAID only means the server has not confirmed payment. It does not prove
  // that an in-flight channel payment failed or that dispatch has started.
  const directTest = order.payment_display?.kind === 'DIRECT_TEST';
  const paymentUnconfirmed = directTest || order.payment_status === 'UNPAID';
  const partialRefund = order.payment_status === 'PARTIALLY_REFUNDED', refunded = order.payment_status === 'REFUNDED';
  const reservationOrigin = order.reservation_origin ? { ...order.reservation_origin,
    startText: date(order.reservation_origin.start_time), endText: date(order.reservation_origin.end_time) } : null;
  const zeroPaymentCancelled = refunded && order.status === 'CANCELLED' && order.amount === 0;
  const expiredPending = !paymentUnconfirmed && order.status === 'PENDING' && order.pending_deadline_expired === true;
  const summary = order.refund_summary;
  const refundRecords = Array.isArray(summary?.records) ? summary.records : [];
  const waitingStaff = order.dispatch_route === 'STAFF' && order.status === 'PENDING' && order.payment_status === 'PAID';
  const waitingPublicDirect = order.dispatch_route === 'PUBLIC' && order.status === 'PENDING' && order.payment_status === 'PAID' && order.requires_boss_selection === false;
  const teamSize = order.required_team_size === 2 ? 2 : 1;
  const participantNames = Array.isArray(order.participants) ? order.participants.map(item => item.nickname).filter(Boolean) : [];
  const deadline = order.status === 'PENDING' && order.payment_status === 'PAID' ? date(order.auto_cancel_at) : '';
  const deadlineDescription = expiredPending ? '已到接单期限，等待处理。请稍后刷新订单；如长时间未更新，请联系客服。' : deadline ? '接单截止时间 ' + deadline + (order.amount === 0 ? '，到期仍未安排将自动取消。' : '，到期仍未安排将自动取消并退回余额。') : '';
  return { ...order, reservationOrigin, isGift, partialRefund, zeroPaymentCancelled, waitingStaff, directTest, paymentUnconfirmed, deadlineDescription: directTest ? '' : deadlineDescription, providerDisplay: paymentUnconfirmed ? '付款状态待确认' : expiredPending ? '已到接单期限，等待处理' : participantNames.length > 1 ? participantNames.join('、') : order.provider_name_snapshot || order.provider?.nickname || (waitingStaff ? (teamSize === 2 ? '等待客服指定两名陪玩' : '等待客服指定陪玩') : waitingPublicDirect ? '等待陪玩直接接单' : order.requires_boss_selection ? '等待你选择陪玩' : '等待平台安排'), fulfillment: directTest ? '支付状态待核对，暂不展示派单及退款进度。' : paymentUnconfirmed ? '付款状态尚未确认，暂不展示派单进度。' : expiredPending ? '已到接单期限，等待处理。' : waitingStaff ? '订单未进入公开接单池，正在等待客服指定' + (teamSize === 2 ? '两名陪玩。' : '陪玩。') : order.fulfillment, service: { ...order.service, name: order.service_name_snapshot || order.service?.name }, statusText: directTest ? '支付状态待核对' : paymentUnconfirmed && order.status !== 'CANCELLED' ? '支付待确认' : expiredPending ? '已到接单期限，等待处理' : waitingStaff ? '等待客服安排' : isGift && order.status === 'COMPLETED' ? '礼物已送达' : statuses[order.status] || '状态更新中', amountText: money(order.amount), originalText: money(order.original_amount), createdText: date(order.created_at),
    refundAmountText: summary ? money(summary.amount) : refunded ? money(order.amount) : '', refundRemainingText: summary?.remaining_amount != null ? money(summary.remaining_amount) : refunded ? '0' : '', refundCreatedText: date(summary?.created_at || order.refunded_at),
    refundRecords: refundRecords.map(item => ({ ...item, amountText: money(item.amount), createdText: date(item.created_at),
      couponResults: (Array.isArray(item.coupon_results) ? item.coupon_results : []).map(coupon => ({ ...coupon,
        text: coupon.outcome === 'RETURNED' ? '已返还，当前状态请查看我的券' : '已核销，不返还' })) })),
    canSelectProvider: !paymentUnconfirmed && !expiredPending && !waitingStaff && allowed.includes('SELECT_PROVIDER'), canCancel: !paymentUnconfirmed && allowed.includes('CANCEL'), canEvaluate: !paymentUnconfirmed && allowed.includes('EVALUATE'), canTip: !paymentUnconfirmed && allowed.includes('TIP'),
    timeline: paymentUnconfirmed ? [{ title: '订单已创建', time: date(order.created_at) }].filter(item => item.time) : isGift ? [{ title: '礼物已送达', time: date(order.completed_at || order.created_at) }] : [
      { title: '下单成功', time: date(order.created_at) },
      { title: '陪玩已接单', time: date(order.grabbed_at) },
      { title: '服务已开始', time: date(order.in_service_at) },
      { title: '服务已完成', time: date(order.completed_at) },
      { title: '订单已取消', time: date(order.cancelled_at) },
      { title: partialRefund ? '部分退款已入账' : '退款已入账', time: zeroPaymentCancelled ? '' : date(summary?.created_at || order.refunded_at) },
    ].filter(item => item.time) };
}
module.exports = { present, statuses };
