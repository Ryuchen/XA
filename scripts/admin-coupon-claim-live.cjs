// Real staff browser, boss HTTP request, ASGI and Redis; the proxy only serves the built frontend.
const fs = require('node:fs'), http = require('node:http'), net = require('node:net'), path = require('node:path'), assert = require('node:assert/strict');
const {chromium} = require('../.tools/club-source-20261003/node_modules/playwright');
const x = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve(x.root), api = new URL(x.base), ws = new URL(x.wsBase);
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const upstream = http.request({hostname: api.hostname, port: api.port, path: req.url, method: req.method, headers: {...req.headers, host: api.host}}, response => {res.writeHead(response.statusCode, response.headers); response.pipe(res)});
    upstream.on('error', () => res.writeHead(502).end()); req.pipe(upstream); return;
  }
  const relative = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, ''), file = path.resolve(root, relative.startsWith('assets/') ? relative : 'index.html');
  if (!file.startsWith(root + path.sep)) {res.writeHead(404).end(); return}
  const type = {'.html':'text/html', '.css':'text/css', '.js':'text/javascript', '.svg':'image/svg+xml'}[path.extname(file)] || 'application/octet-stream';
  fs.readFile(file, (error, data) => error ? res.writeHead(404).end() : res.writeHead(200, {'Content-Type':type}).end(data));
});
server.on('upgrade', (req, socket, head) => {
  const upstream = net.connect(Number(ws.port), ws.hostname, () => {const headers = req.rawHeaders.reduce((out, value, i, all) => i % 2 ? out : out + `${value}: ${all[i+1]}\r\n`, ''); upstream.write(`${req.method} ${req.url} HTTP/${req.httpVersion}\r\n${headers}\r\n`); if (head.length) upstream.write(head); socket.pipe(upstream).pipe(socket)});
  upstream.on('error', () => socket.destroy()); socket.on('error', () => upstream.destroy());
});
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve)); let browser;
  try {
    browser = await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless:true});
    const context = await browser.newContext({viewport:x.mobile ? {width:375,height:812} : {width:1280,height:900}, isMobile:!!x.mobile, hasTouch:!!x.mobile});
    await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), x.staffToken);
    const page = await context.newPage(), errors = []; page.on('pageerror', e => errors.push(e.message));
    let connected; const ready = new Promise(resolve => {connected = resolve});
    page.on('websocket', socket => socket.on('framereceived', frame => {if (String(frame.payload).includes('pong')) connected()}));
    await page.goto(`http://127.0.0.1:${server.address().port}/coupons`);
    const row = page.locator('tr').filter({hasText:'客服实时领券'});
    await row.getByText('已领 0 · 发放 3').waitFor({timeout:15000});
    await Promise.race([ready, new Promise((_, reject) => setTimeout(() => reject(Error('admin websocket timeout')), 10000))]);
    await row.getByRole('button', {name:'领取记录'}).click();
    const dialog = page.getByRole('dialog'); await dialog.getByText('暂无记录').waitFor();
    const response = await fetch(`${x.base}/api/coupons/${x.couponId}/claim/`, {method:'POST', headers:{Authorization:'Bearer '+x.bossToken}});
    const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result));
    await dialog.getByText(x.bossName || '实时领券老板').waitFor({timeout:10000});
    const holder = dialog.locator('tr').filter({hasText:x.bossName || '实时领券老板'});
    await holder.getByText('未使用', {exact:true}).waitFor({timeout:10000});
    if (x.mobile) assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    async function bossRequest(url, method, data) {
      const response = await fetch(x.base + url, {method, headers:{Authorization:'Bearer '+x.bossToken, 'Content-Type':'application/json'}, ...(data ? {body:JSON.stringify(data)} : {})});
      const body = await response.json(); assert.equal(body.code, 0, JSON.stringify(body)); return body.data;
    }
    const mine = await bossRequest('/api/coupons/mine/', 'GET');
    const voucher = mine.find(item => item.coupon === x.couponId || item.coupon_id === x.couponId || item.name === '客服实时领券');
    assert.ok(voucher, JSON.stringify(mine));
    const order = await bossRequest('/api/orders/create/', 'POST', {service_id:x.serviceId, user_coupon_id:voucher.id, dispatch_route:'STAFF', client_request_id:'admin-coupon-live-order'});
    await holder.getByText('已使用', {exact:true}).waitFor({timeout:10000});
    await holder.getByText(order.order_no, {exact:true}).waitFor({timeout:10000});
    await bossRequest(`/api/orders/orders/${order.id}/cancel/`, 'POST', {reason:'隔离业务验收'});
    await holder.getByText('未使用', {exact:true}).waitFor({timeout:10000});
    async function changeTemplate(active) {
      const response = await fetch(`${x.base}/api/admin/coupons/${x.couponId}/`, {method:'PATCH', headers:{Authorization:'Bearer '+x.editorToken, 'Content-Type':'application/json'}, body:JSON.stringify({is_active:active})});
      const body = await response.json(); assert.equal(body.code, 0, JSON.stringify(body));
    }
    await changeTemplate(false);
    await holder.getByText('模板停用', {exact:true}).waitFor({timeout:10000});
    await row.getByText('模板停用', {exact:true}).waitFor({state:'attached', timeout:10000});
    await changeTemplate(true);
    await holder.getByText('未使用', {exact:true}).waitFor({timeout:10000});
    await row.getByText('模板启用', {exact:true}).waitFor({state:'attached', timeout:10000});
    await page.keyboard.press('Escape');
    await row.getByText('已领 1 · 发放 3').waitFor({timeout:10000});
    const createdResponse = await fetch(`${x.base}/api/admin/coupons/`, {method:'POST', headers:{Authorization:'Bearer '+x.editorToken, 'Content-Type':'application/json'}, body:JSON.stringify({name:'另一客服新建券', discount_type:'DIRECT', amount:500})});
    const created = await createdResponse.json(); assert.equal(created.code, 0, JSON.stringify(created));
    const newRow = page.locator('tr').filter({hasText:'另一客服新建券'});
    await newRow.waitFor({timeout:10000});
    const removedResponse = await fetch(`${x.base}/api/admin/coupons/${created.data.id}/`, {method:'DELETE', headers:{Authorization:'Bearer '+x.editorToken}});
    const removed = await removedResponse.json(); assert.equal(removed.code, 0, JSON.stringify(removed));
    await newRow.waitFor({state:'detached', timeout:10000});
    assert.deepEqual(errors, []);
  } finally {if (browser) await browser.close(); server.closeAllConnections(); server.close()}
})().catch(error => {console.error(error.stack || error); process.exitCode = 1; server.closeAllConnections(); server.close()});
