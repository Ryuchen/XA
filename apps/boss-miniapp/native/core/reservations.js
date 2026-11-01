const { date, money } = require('./ui');
const statuses = { PENDING: '待陪玩确认', CONFIRMED: '已确认', REJECTED: '已拒绝', CANCELLED: '已取消', CONVERTED: '已成单', EXPIRED: '已过期' };
function present(row) {
  const actions = Array.isArray(row.allowed_actions) ? row.allowed_actions : [];
  return { ...row, statusText: statuses[row.status] || row.status, startText: date(row.start_time), endText: date(row.end_time),
    estimateText: money(row.estimated_amount), canCancel: actions.includes('cancel'), canConvert: actions.includes('convert') };
}
module.exports = { present };
