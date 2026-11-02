// Real browser, HTTP and WebSocket against one isolated ASGI process.
const fs = require('node:fs'), http = require('node:http'), net = require('node:net');
const path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(process.env.XA_PROVIDER_ASSIGNMENT_BUILD || '.tools/provider-assignment-alert-20261101'), upstream = new URL(input.api);
const socketClients = new Set();
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const proxy = http.request({ hostname: upstream.hostname, port: upstream.port, path: req.url, method: req.method,
      headers: { ...req.headers, host: upstream.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
    proxy.on('error', () => res.writeHead(502).end()); req.pipe(proxy); return;
  }
  const file = path.resolve(root, decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html');
  if (!file.startsWith(root + path.sep)) { res.writeHead(404).end(); return; }
  fs.readFile(file, (error, data) => { if (error) { res.writeHead(404).end(); return; }
    res.writeHead(200, { 'Content-Type': { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript' }[path.extname(file)] || 'application/octet-stream' }).end(data); });
});
server.on('upgrade', (req, client, head) => {
  socketClients.add(client); client.on('close', () => socketClients.delete(client));
  const upstreamSocket = net.connect(Number(upstream.port), upstream.hostname, () => {
    const headers = req.rawHeaders.reduce((s, item, index, all) => index % 2 ? s : s + `${item}: ${all[index + 1]}\r\n`, '');
    upstreamSocket.write(`${req.method} ${req.url} HTTP/${req.httpVersion}\r\n${headers}\r\n`);
    if (head.length) upstreamSocket.write(head);
    client.pipe(upstreamSocket).pipe(client);
  });
  upstreamSocket.on('error', () => client.destroy()); client.on('error', () => upstreamSocket.destroy());
  client.on('close', () => upstreamSocket.destroy());
});
async function post(url, token, data) {
  const response = await fetch(input.api + url, { method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
  const body = await response.json(); assert.equal(body.code, 0, JSON.stringify(body)); return body.data;
}
async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(({ token, id }) => {
      localStorage.setItem('token', JSON.stringify({ data: token }));
      localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩' } }));
      window.__soundCount = 0;
      const original = AudioContext.prototype.createOscillator;
      AudioContext.prototype.createOscillator = function() { window.__soundCount++; return original.call(this); };
    }, { token: input.providerToken, id: input.providerId });
    const page = await context.newPage(), errors = [], frames = [];
    let socketOpened = false, closedSockets = 0;
    page.on('pageerror', error => errors.push(error.message));
    page.on('websocket', ws => { ws.on('framereceived', frame => { try { frames.push(JSON.parse(frame.payload)); } catch {} }); ws.on('close', () => closedSockets++); socketOpened = true; });
    await page.goto(`http://127.0.0.1:${server.address().port}/#/pages/orders/index`);
    await page.getByText('订单提示音 开', { exact: true }).waitFor({ timeout: 20000 });
    await page.getByText('暂时没有可接订单').waitFor();
    await page.getByText('订单提示音 开', { exact: true }).click();
    await page.getByText('订单提示音 关', { exact: true }).click();
    for (let i = 0; i < 100 && !socketOpened; i++) await new Promise(resolve => setTimeout(resolve, 50));
    assert.equal(socketOpened, true, 'WebSocket opened');
    // Ping/pong proves the ASGI upgrade is ready before dispatch.
    for (let i = 0; i < 100 && !frames.some(frame => frame.type === 'pong'); i++) await new Promise(resolve => setTimeout(resolve, 50));
    assert.ok(frames.some(frame => frame.type === 'pong'), 'real ASGI socket heartbeat');
    for (const [index, muted] of [[1, false], [2, true]]) {
      if (muted) await page.getByText('订单提示音 开', { exact: true }).click();
      const order = await post('/api/orders/create/', input.bossToken, { service_id: input.serviceId, dispatch_route: 'STAFF', client_request_id: `sound-assignment-${index}` });
      await post(`/api/admin/orders/${order.id}/assign/`, input.staffToken, { provider_id: Number(input.providerId) });
      for (let i = 0; i < 100 && frames.filter(frame => frame.type === 'site_message_update' && frame.data?.assignment_message_id).length < index; i++) await new Promise(resolve => setTimeout(resolve, 50));
      assert.equal(frames.filter(frame => frame.type === 'site_message_update' && frame.data?.assignment_message_id).length, index);
      if (!muted) {
        await page.waitForFunction(() => window.__soundCount === 3, null, { timeout: 5000 });
        // Return the first staff order through the real rejection workflow so
        // this provider is available for the next assignment.
        await post(`/api/orders/orders/${order.id}/reject/`, input.providerToken, { reason: '隔离验收调整' });
      }
      else {
        await new Promise(resolve => setTimeout(resolve, 500)); assert.equal(await page.evaluate(() => window.__soundCount), 3);
        await post(`/api/orders/orders/${order.id}/reject/`, input.providerToken, { reason: '继续重连验收' });
      }
    }
    await page.getByText('订单提示音 关', { exact: true }).click();
    await context.setOffline(true);
    for (const client of socketClients) client.destroy();
    for (let i = 0; i < 100 && !closedSockets; i++) await new Promise(resolve => setTimeout(resolve, 50));
    assert.ok(closedSockets, 'browser socket really disconnected');
    const missed = await post('/api/orders/create/', input.bossToken, { service_id: input.serviceId, dispatch_route: 'STAFF', client_request_id: 'sound-assignment-offline' });
    await post(`/api/admin/orders/${missed.id}/assign/`, input.staffToken, { provider_id: Number(input.providerId) });
    assert.equal(await page.evaluate(() => window.__soundCount), 3);
    await context.setOffline(false);
    await page.waitForFunction(() => window.__soundCount === 6, null, { timeout: 20000 });
    await new Promise(resolve => setTimeout(resolve, 1000));
    assert.equal(await page.evaluate(() => window.__soundCount), 6, 'reconnect must not repeat alert');
    await post(`/api/orders/orders/${missed.id}/reject/`, input.providerToken, { reason: '准备报名验收' });
    // Suppress the public-pool bell so this checks the subsequent selection bell independently.
    await page.getByText('订单提示音 开', { exact: true }).click();
    const selection = await post('/api/orders/create/', input.bossToken, { service_id: input.serviceId,
      dispatch_route: 'PUBLIC', dispatch_mode: 'BOSS_SELECT', client_request_id: 'sound-boss-selection' });
    await page.getByText(selection.order_no, { exact: true }).waitFor({ timeout: 20000 });
    await post(`/api/orders/orders/${selection.id}/grab/`, input.providerToken, {});
    const response = await fetch(`${input.api}/api/orders/orders/${selection.id}/`, { headers: { Authorization: `Bearer ${input.bossToken}` } });
    const detail = await response.json(); assert.equal(detail.code, 0, JSON.stringify(detail));
    assert.equal(detail.data.applications.length, 1);
    await page.getByText('订单提示音 关', { exact: true }).click();
    await post(`/api/orders/orders/${selection.id}/applications/${detail.data.applications[0].id}/select/`, input.bossToken, {});
    await page.waitForFunction(() => window.__soundCount === 9, null, { timeout: 5000 });
    await new Promise(resolve => setTimeout(resolve, 1000));
    assert.equal(await page.evaluate(() => window.__soundCount), 9, 'selection rings only once');
    await page.getByText('订单提示音 开', { exact: true }).click();
    await post(`/api/orders/orders/${selection.id}/reject/`, input.providerToken, { reason: '准备预约验收' });
    await page.getByText(selection.order_no, { exact: true }).waitFor({ timeout: 20000 });
    await page.getByText('订单提示音 关', { exact: true }).click();
    const start = new Date(Math.ceil((Date.now() + 48 * 60 * 60 * 1000) / (15 * 60 * 1000)) * 15 * 60 * 1000);
    const end = new Date(start.getTime() + 60 * 60 * 1000);
    const reservation = await post('/api/orders/reservations/', input.bossToken, {
      service_id: input.serviceId, provider_account_id: Number(input.providerId),
      start_time: start.toISOString(), end_time: end.toISOString(),
    });
    await post(`/api/orders/reservations/${reservation.id}/confirm/`, input.providerToken, {});
    const quoteResponse = await fetch(`${input.api}/api/orders/reservations/${reservation.id}/quote/`, {
      headers: { Authorization: `Bearer ${input.bossToken}` },
    });
    const quote = await quoteResponse.json(); assert.equal(quote.code, 0, JSON.stringify(quote));
    const converted = await post(`/api/orders/reservations/${reservation.id}/convert/`, input.bossToken, {
      client_request_id: 'sound-reservation-conversion', expected_amount: quote.data.amount, expected_service: quote.data.service,
    });
    assert.equal(converted.order.status, 'GRABBED');
    await page.waitForFunction(() => window.__soundCount === 12, null, { timeout: 5000 });
    await new Promise(resolve => setTimeout(resolve, 1000));
    assert.equal(await page.evaluate(() => window.__soundCount), 12, 'reservation conversion rings only once');
    await page.getByText('订单提示音 开', { exact: true }).click();
    await post(`/api/orders/orders/${converted.order.id}/reject/`, input.providerToken, { reason: '准备指定下单验收' });
    await new Promise(resolve => setTimeout(resolve, 700));
    await page.getByText('订单提示音 关', { exact: true }).click();
    const direct = await post('/api/orders/create/', input.bossToken, {
      service_id: input.serviceId, provider_id: Number(input.providerId), client_request_id: 'sound-boss-direct',
    });
    assert.equal(direct.status, 'GRABBED');
    await page.waitForFunction(() => window.__soundCount === 15, null, { timeout: 5000 });
    await new Promise(resolve => setTimeout(resolve, 1000));
    assert.equal(await page.evaluate(() => window.__soundCount), 15, 'boss direct assignment rings only once');
    await page.getByText('订单提示音 开', { exact: true }).click();
    await post(`/api/orders/orders/${direct.id}/reject/`, input.providerToken, { reason: '准备历史派单入口验收' });
    await new Promise(resolve => setTimeout(resolve, 700));
    await page.getByText('订单提示音 关', { exact: true }).click();
    const legacyOrder = await post('/api/orders/create/', input.bossToken, {
      service_id: input.serviceId, dispatch_route: 'STAFF', client_request_id: 'sound-legacy-assignment',
    });
    await post(`/api/orders/orders/${legacyOrder.id}/assign/`, input.staffToken, { provider_id: Number(input.providerId) });
    await page.waitForFunction(() => window.__soundCount === 18, null, { timeout: 5000 });
    await new Promise(resolve => setTimeout(resolve, 1000));
    assert.equal(await page.evaluate(() => window.__soundCount), 18, 'legacy staff assignment rings only once');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.deepEqual(errors, []);
    console.log('Both staff assignment endpoints, reconnect, boss selection and reservation conversion use the real provider H5 sound');
  } finally { await browser.close(); server.closeAllConnections(); server.close(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; server.close(); });
