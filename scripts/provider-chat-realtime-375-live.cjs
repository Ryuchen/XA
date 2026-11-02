// Real provider H5 + isolated ASGI HTTP/WebSocket; no real recipients.
const fs = require('node:fs'), http = require('node:http'), net = require('node:net'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(process.env.XA_PROVIDER_CHAT_H5_ROOT), target = new URL(input.api);
const types = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.png': 'image/png', '.svg': 'image/svg+xml' };
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const proxy = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method, headers: { ...req.headers, host: target.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
    proxy.on('error', () => res.writeHead(502).end()); req.pipe(proxy); return;
  }
  const relative = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html';
  const file = path.resolve(root, relative);
  if (!file.startsWith(root + path.sep)) { res.writeHead(404).end(); return; }
  fs.readFile(file, (error, data) => error ? res.writeHead(404).end() : res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'application/octet-stream' }).end(data));
});
server.on('upgrade', (req, socket, head) => {
  const upstream = net.connect(Number(target.port), target.hostname, () => {
    const headers = req.rawHeaders.reduce((result, value, index, all) => index % 2 ? result : result + `${value}: ${all[index + 1]}\r\n`, '');
    upstream.write(`${req.method} ${req.url} HTTP/${req.httpVersion}\r\n${headers}\r\n`);
    if (head.length) upstream.write(head);
    socket.pipe(upstream).pipe(socket);
  });
  upstream.on('error', () => socket.destroy()); socket.on('error', () => upstream.destroy());
});
async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(({ token, id }) => {
      localStorage.setItem('token', JSON.stringify({ data: token }));
      localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩' } }));
    }, input);
    const page = await context.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let socketReady; const ready = new Promise(resolve => { socketReady = resolve; });
    page.on('websocket', ws => ws.on('framereceived', frame => { if (String(frame.payload).includes('pong')) socketReady(); }));
    await page.goto(`http://127.0.0.1:${server.address().port}/#/pages/support-chat/index`);
    await page.getByText('写下你想咨询的问题', { exact: true }).waitFor({ timeout: 20000 });
    let readyTimeout;
    try { await Promise.race([ready, new Promise((_, reject) => { readyTimeout = setTimeout(() => reject(Error('provider websocket did not connect')), 15000); })]); }
    finally { clearTimeout(readyTimeout); }
    const reply = async content => {
      const response = await fetch(`${input.api}/api/admin/chat-sessions/${input.sessionId}/reply/`, {
        method: 'POST', headers: { Authorization: `Bearer ${input.staffToken}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ content }),
      });
      const data = await response.json(); assert.equal(data.code, 0, JSON.stringify(data));
    };
    const unread = async () => (await (await fetch(`${input.api}/api/chat/unread/`, { headers: { Authorization: `Bearer ${input.token}` } })).json()).data.unread;
    const waitUnread = async expected => {
      for (let attempt = 0; attempt < 100; attempt++) {
        if (await unread() === expected) return;
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      throw Error(`unread did not become ${expected}`);
    };
    await reply('隔离客服实时回复');
    await page.getByText('隔离客服实时回复', { exact: true }).waitFor({ timeout: 10000 });
    await waitUnread(0);
    await page.goto(`http://127.0.0.1:${server.address().port}/#/pages/orders/index`);
    await reply('隔离客服离开后回复');
    assert.equal(await unread(), 1);
    await page.goto(`http://127.0.0.1:${server.address().port}/#/pages/support-chat/index`);
    await page.getByText('隔离客服离开后回复', { exact: true }).waitFor({ timeout: 10000 });
    await waitUnread(0);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.deepEqual(errors, []);
  } finally { if (browser) await browser.close(); server.closeAllConnections(); server.close(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; server.closeAllConnections(); server.close(); });
