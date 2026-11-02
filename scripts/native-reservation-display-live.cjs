// Render actual boss reservation API responses using the native presenter.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { present } = require('../apps/boss-miniapp/native/core/reservations');

for (const row of JSON.parse(fs.readFileSync(0, 'utf8'))) {
  const item = present(row.reservation);
  assert.equal(item.status, 'REJECTED');
  assert.equal(item.statusText, '已拒绝');
  assert.equal(item.startText, row.startText);
  assert.equal(item.canConvert, false);
}
