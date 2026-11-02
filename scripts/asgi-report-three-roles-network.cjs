// Real HTTP and WebSocket traffic to one isolated Daphne process.
const assert = require('node:assert/strict'), fs = require('node:fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), base = `http://127.0.0.1:${input.port}`;
const sockets = [], received = input.tokens.map(() => []);
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function connect(index) {
  const socket = new WebSocket(`ws://127.0.0.1:${input.port}/ws/orders/?token=${encodeURIComponent(input.tokens[index])}`);
  sockets.push(socket); socket.addEventListener('message', event => received[index].push(JSON.parse(event.data)));
  await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', reject, { once: true }); });
}
async function request(actor, pathname, method = 'GET', body) {
  const response = await fetch(base + pathname, { method, headers: { Authorization: `Bearer ${input.tokens[actor]}`, 'Content-Type': 'application/json' }, ...(body ? { body: JSON.stringify(body) } : {}) });
  const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result)); return result.data;
}
async function eventFor(actor, type, status) {
  for (let attempt = 0; attempt < 160; attempt++) {
    const index = received[actor].findIndex(event => event.type === type && (!status || event.data.status === status));
    if (index >= 0) return received[actor].splice(index, 1)[0].data;
    await delay(25);
  }
  throw new Error(`missing ${type}/${status} for ${actor}: ${JSON.stringify(received[actor])}`);
}
(async () => {
  await Promise.all(input.tokens.map((_, i) => connect(i)));
  for (const [actor, action, status, hasClock] of [[1, 'submit', 'PENDING', true], [2, 'reject', 'REJECTED', false], [1, 'submit', 'PENDING', true]]) {
    const url = action === 'reject' ? `/api/admin/reports/${input.report}/reject/` : `/api/wallet/reports/${input.report}/submit/`;
    await request(actor, url, 'POST', action === 'reject' ? { audit_remark: '隔离验收：截图不清晰' } : {});
    for (const recipient of [1, 2]) assert.equal((await eventFor(recipient, 'report_status_update', status)).report_id, input.report);
    const update = await eventFor(0, 'order_status_update');
    const actual = await request(0, `/api/orders/orders/${input.order}/`);
    assert.equal(update.id, input.order); assert.equal(update.consumption_ready_at, actual.consumption_ready_at);
    assert.equal(Boolean(actual.consumption_ready_at), hasClock);
  }
  await request(2, `/api/admin/reports/${input.report}/approve/`, 'POST', {});
  for (const recipient of [1, 2]) assert.equal((await eventFor(recipient, 'report_status_update', 'APPROVED')).order_id, input.order);
  console.log('Real network report submit/reject/resubmit/approve signals reached three roles');
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; }).finally(() => sockets.forEach(socket => socket.close()));
