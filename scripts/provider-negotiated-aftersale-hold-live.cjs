// Real provider UI, isolated HTTP and Redis/WebSocket; only the staff action is issued by HTTP.
const fs = require('node:fs'), path = require('node:path'), http = require('node:http'), net = require('node:net'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const x = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve(x.root), api = new URL(x.base), ws = new URL(x.wsBase);
const nativeRoot = path.resolve('apps/boss-miniapp/native'), storage = new Map();
let bossConnected;
const bossReady = new Promise(resolve => { bossConnected = resolve; });
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: key => storage.delete(key), getStorageInfoSync: () => ({ keys: [...storage.keys()] }),
  getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }), stopPullDownRefresh() {},
  request(options) { const url = new URL(options.url); fetch(x.base + url.pathname + url.search, { method: options.method, headers: options.header, ...(options.data === undefined ? {} : { body: JSON.stringify(options.data) }) }).then(async response => options.success({ statusCode: response.status, data: await response.json() })).catch(options.fail); },
  connectSocket(options) { const url = new URL(options.url), socket = new WebSocket(`ws://${ws.host}${url.pathname}${url.search}`); return {
    onOpen: callback => socket.addEventListener('open', callback), onMessage: callback => socket.addEventListener('message', event => { if (String(event.data).includes('pong')) bossConnected(); callback({ data: event.data }); }),
    onClose: callback => socket.addEventListener('close', callback), onError: callback => socket.addEventListener('error', callback), send: value => socket.send(value.data), close: () => socket.close(),
  }; },
};
let definition;
global.Page = value => { definition = value; };
require(path.join(nativeRoot, 'core/auth')).setSession({ token: x.bossToken, user: { id: String(x.bossId), role: 'customer' } });
const realtime = require(path.join(nativeRoot, 'core/realtime'));
require(path.join(nativeRoot, 'pages/orders/index.js'));
const bossList = { ...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
for (const [key, value] of Object.entries(bossList)) if (typeof value === 'function') bossList[key] = value.bind(bossList);
async function waitForBoss(predicate) { for (let i = 0; i < 400; i++) { if (predicate()) return; await new Promise(resolve => setTimeout(resolve, 25)); } throw Error('boss order list did not update'); }
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const upstream = http.request({ hostname: api.hostname, port: api.port, path: req.url, method: req.method, headers: { ...req.headers, host: api.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
    upstream.on('error', () => res.writeHead(502).end()); req.pipe(upstream); return;
  }
  const file = path.resolve(root, decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html');
  if (!file.startsWith(root + path.sep)) return res.writeHead(404).end();
  fs.readFile(file, (error, data) => error ? res.writeHead(404).end() : res.writeHead(200, { 'Content-Type': { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' }[path.extname(file)] || 'application/octet-stream' }).end(data));
});
server.on('upgrade', (req, socket, head) => {
  const peer = net.connect(Number(ws.port), ws.hostname, () => {
    peer.write(`${req.method} ${req.url} HTTP/${req.httpVersion}\r\n`);
    for (let i = 0; i < req.rawHeaders.length; i += 2) peer.write(`${req.rawHeaders[i]}: ${req.rawHeaders[i + 1]}\r\n`);
    peer.write('\r\n'); if (head.length) peer.write(head); socket.pipe(peer).pipe(socket);
  });
  peer.on('error', () => socket.destroy()); socket.on('error', () => peer.destroy());
});
(async () => {
  let browser;
  try {
    bossList.onLoad(); await bossList.onShow(); realtime.show();
    await Promise.race([bossReady, new Promise((_, reject) => setTimeout(() => reject(Error('boss websocket unavailable')), 15000))]);
    const bossOrder = () => bossList.data.items.find(item => item.id === x.orderId);
    assert.equal(bossOrder()?.aftersaleProgress, '售后处理中');
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(({ id, token }) => { localStorage.setItem('token', JSON.stringify({ data: token })); localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id: String(id), role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩' } })); }, x);
    const page = await context.newPage(), errors = []; page.on('pageerror', error => errors.push(error.message));
    const ready = new Promise((resolve, reject) => { const timer = setTimeout(() => reject(Error('provider websocket unavailable')), 15000); page.on('websocket', socket => socket.on('framereceived', frame => { if (String(frame.payload).includes('pong')) { clearTimeout(timer); resolve(); } })); });
    await page.goto(`http://127.0.0.1:${server.address().port}/#/orders?order=${x.orderId}`); await ready;
    const card = page.locator('.order-card').filter({ hasText: x.orderNo });
    await card.getByText('售后处理中，暂不能结单，请等待客服处理', { exact: true }).waitFor({ timeout: 15000 });
    assert.equal(await card.getByRole('button', { name: '完成订单' }).count(), 0);
    const response = await fetch(`${x.base}/api/admin/aftersales/${x.caseId}/resolve/`, { method: 'POST', headers: { Authorization: `Bearer ${x.staffToken}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ resolution: '双方协商继续服务' }) });
    const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result));
    await card.getByRole('button', { name: '完成订单' }).waitFor({ timeout: 15000 });
    await waitForBoss(() => bossOrder()?.aftersaleProgress === '' && bossOrder()?.status === 'IN_SERVICE');
    assert.equal(await card.getByText('售后处理中，暂不能结单，请等待客服处理', { exact: true }).count(), 0);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true); assert.deepEqual(errors, []);
    console.log('Real provider page restored completion after staff resolved aftersale');
  } finally { realtime.hide(); bossList.onUnload(); if (browser) await browser.close(); server.closeAllConnections(); server.close(); }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
