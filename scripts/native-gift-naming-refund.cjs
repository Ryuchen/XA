const assert = require('node:assert/strict');
const fs = require('node:fs');
const { present } = require('../apps/boss-miniapp/native/core/orders');
const { date } = require('../apps/boss-miniapp/native/core/ui');
const details = JSON.parse(fs.readFileSync(0, 'utf8'));
for (const detail of details) {
  const result = detail.refund_summary.records[0].gift_naming_result;
  const shown = present(detail).refundRecords[0].namingResultText;
  assert.equal(shown, result.remaining_paid_grants
    ? '本次处理后冠名截止：' + date(result.expires_at)
    : '本次处理后该冠名无剩余赠礼时长');
}
