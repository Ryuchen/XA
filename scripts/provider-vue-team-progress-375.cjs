const fs = require('node:fs'), http = require('node:http'), net = require('node:net'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve(input.root), target = new URL(input.base);
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) { const proxy = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method, headers: { ...req.headers, host: target.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); }); proxy.on('error', () => res.writeHead(502).end()); req.pipe(proxy); return; }
  const file = path.resolve(root, decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html');
  if (!file.startsWith(root + path.sep)) return res.writeHead(404).end();
  fs.readFile(file, (error, data) => error ? res.writeHead(404).end() : res.writeHead(200, { 'Content-Type': { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript' }[path.extname(file)] || 'application/octet-stream' }).end(data));
});
const sockets = new Set();
server.on('upgrade', (req, client, head) => {
  const upstream = new URL(input.wsBase || input.base); sockets.add(client); client.on('close', () => sockets.delete(client));
  const peer = net.connect(Number(upstream.port), upstream.hostname, () => {
    const headers = req.rawHeaders.reduce((s, item, i, all) => i % 2 ? s : s + `${item}: ${all[i + 1]}\r\n`, '');
    peer.write(`${req.method} ${req.url} HTTP/${req.httpVersion}\r\n${headers}\r\n`); if (head.length) peer.write(head); client.pipe(peer).pipe(client);
  });
  peer.on('error', () => client.destroy()); client.on('error', () => peer.destroy()); client.on('close', () => peer.destroy());
});
async function api(url, token, form) { const response = await fetch(input.base + url, { method: form ? 'POST' : 'GET', headers: { Authorization: `Bearer ${token}` }, ...(form ? { body: form } : {}) }); const data = await response.json(); assert.equal(data.code, 0, JSON.stringify(data)); return data.data; }
const proof = field => { const form = new FormData(); form.append(field, new Blob([fs.readFileSync(input.image)], { type: 'image/png' }), 'proof.png'); return form; };
async function check(stage, count) {
  let expected;
  for (const [role, token] of Object.entries(input.tokens)) {
    const prefix = role === 'staff' ? '/api/admin/orders/' : '/api/orders/orders/';
    const row = await api(`${prefix}${input.orderId}/`, token);
    assert.equal(row.team_progress.stage, stage); assert.equal(row.team_progress.submitted_count, count);
    if (!expected) expected = row.team_progress.members;
    else assert.deepEqual(row.team_progress.members, expected);
  }
  return expected;
}
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const contexts = [];
    for (const provider of input.providers) {
      const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
      await context.addInitScript(({ token, id }) => { localStorage.setItem('token', JSON.stringify({ data: token })); localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩' } })); }, provider);
      const page = await context.newPage(), frames = [];
      page.on('websocket', ws => ws.on('framereceived', frame => { try { frames.push(JSON.parse(frame.payload)); } catch {} }));
      await page.goto(`http://127.0.0.1:${server.address().port}/#/orders?order=${input.orderId}`); contexts.push({ context, page, frames });
    }
    async function visible(text, members) { for (const { page } of contexts) { await page.getByText(text, { exact: true }).waitFor({ timeout: 15000 }); for (const member of members) await page.getByText(`${member.nickname} · ${member.submitted ? '已提交' : '等待提交'}`, { exact: true }).waitFor(); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true); } }
    async function refresh() { for (const { page } of contexts) await page.getByRole('button', { name: '刷新', exact: true }).click(); }
    await visible('入队截图 · 1/2 已提交', await check('ENTRY', 1));
    if (input.realtime) for (const { frames } of contexts) { for (let i = 0; i < 100 && !frames.some(frame => frame.type === 'pong'); i++) await new Promise(resolve => setTimeout(resolve, 50)); assert.ok(frames.some(frame => frame.type === 'pong'), 'real WebSocket heartbeat'); }
    async function updates(count) { if (!input.realtime) return; for (const { frames } of contexts) { for (let i = 0; i < 100 && frames.filter(frame => frame.type === 'order_status_update').length < count; i++) await new Promise(resolve => setTimeout(resolve, 50)); assert.ok(frames.filter(frame => frame.type === 'order_status_update').length >= count, 'order event delivered'); } }
    await api(`/api/orders/orders/${input.orderId}/start/`, input.providers[1].token, proof('entry_image'));
    await updates(1);
    if (!input.realtime) await refresh(); await visible('结单截图 · 0/2 已提交', await check('COMPLETION', 0));
    await api(`/api/orders/orders/${input.orderId}/complete/`, input.providers[0].token, proof('completion_image'));
    await updates(2);
    if (!input.realtime) await refresh(); await visible('结单截图 · 1/2 已提交', await check('COMPLETION', 1));
    await api(`/api/orders/orders/${input.orderId}/complete/`, input.providers[1].token, proof('completion_image'));
    await updates(3);
    if (!input.realtime) await refresh();
    for (const [role, token] of Object.entries(input.tokens)) {
      const prefix = role === 'staff' ? '/api/admin/orders/' : '/api/orders/orders/';
      const row = await api(`${prefix}${input.orderId}/`, token);
      assert.equal(row.status, 'COMPLETED'); assert.equal(row.team_progress, null);
    }
    for (const { page } of contexts) {
      await page.locator('.order-card').getByText('已完成', { exact: true }).waitFor({ timeout: 15000 });
      assert.equal(await page.getByText('结单截图 · 1/2 已提交', { exact: true }).count(), 0);
    }
    for (const { context } of contexts) await context.close();
  } finally { await browser.close(); for (const client of sockets) client.destroy(); server.closeAllConnections(); server.close(); }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
