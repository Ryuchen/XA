// Render the real provider H5 against the isolated Django LiveServer.
const fs = require('node:fs'), http = require('node:http'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve('.tools/provider-chat-h5-image-restore-20261101');
const target = new URL(input.base);
const types = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.png': 'image/png', '.svg': 'image/svg+xml' };
let dropNextSendResponse = false, droppedResponse = null, resolveDropped;
const responseDropped = new Promise(resolve => { resolveDropped = resolve; });
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const proxy = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method, headers: { ...req.headers, host: target.host } }, response => {
      if (dropNextSendResponse && req.url === '/api/chat/send/' && req.method === 'POST') {
        const chunks = []; response.on('data', chunk => chunks.push(chunk)); response.on('end', () => { droppedResponse = JSON.parse(Buffer.concat(chunks).toString()); res.destroy(); resolveDropped(); });
      } else { res.writeHead(response.statusCode, response.headers); response.pipe(res); }
    });
    proxy.on('error', () => res.writeHead(502).end()); req.pipe(proxy); return;
  }
  const relative = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html';
  const file = path.resolve(root, relative);
  if (!file.startsWith(root + path.sep)) { res.writeHead(404).end(); return; }
  fs.readFile(file, (error, data) => { if (error) { res.writeHead(404).end(); return; } res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'application/octet-stream' }).end(data); });
});
async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(({ token, id }) => {
      localStorage.setItem('token', JSON.stringify({ data: token }));
      localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩', avatar: '' } }));
    }, { token: input.providerToken, id: input.providerId });
    const page = await context.newPage();
    const base = `http://127.0.0.1:${server.address().port}`;
    const origins = new Set([base, target.origin]);
    await page.route('**/*', route => origins.has(new URL(route.request().url()).origin) ? route.continue() : route.abort());
    await page.goto(`${base}/#/pages/messages/index`);
    await page.getByText('平台客服对话', { exact: true }).click({ timeout: 15000 });
    await page.getByText('站内咨询', { exact: true }).waitFor({ timeout: 15000 });
    await page.getByText('历史客服消息 50', { exact: true }).waitFor({ timeout: 15000 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, 'horizontal overflow');
    const send = page.getByText('发送', { exact: true });
    const bounds = await send.boundingBox(); assert.ok(bounds && bounds.width >= 44 && bounds.height >= 44, JSON.stringify(bounds));
    await page.locator('textarea').fill('小屏验收咨询'); await send.click();
    await page.getByText('小屏验收咨询', { exact: true }).waitFor({ timeout: 15000 });
    await page.locator('textarea').fill('排班截图，麻烦核对');
    const brokenChooserPromise = page.waitForEvent('filechooser');
    await page.getByText('发送图片', { exact: true }).click();
    const brokenChooser = await brokenChooserPromise;
    const refused = page.waitForResponse(response => response.url().includes('/api/chat/send/') && response.request().method() === 'POST', { timeout: 15000 });
    await brokenChooser.setFiles(input.brokenImage);
    const refusal = await (await refused).json();
    assert.equal(refusal.code, 400);
    assert.equal(refusal.msg, '图片内容无效');
    await page.getByText('图片内容无效', { exact: true }).waitFor({ timeout: 5000 });
    assert.equal(await page.locator('textarea').inputValue(), '排班截图，麻烦核对');
    assert.equal(await page.getByText('上条消息结果未确认，请先核对。').count(), 0);
    const chooserPromise = page.waitForEvent('filechooser');
    await page.getByText('发送图片', { exact: true }).click();
    const chooser = await chooserPromise;
    const uploaded = page.waitForResponse(response => response.url().includes('/api/chat/send/') && response.request().method() === 'POST' && response.status() === 200, { timeout: 15000 });
    await chooser.setFiles(path.resolve('apps/boss-miniapp/native/assets/icons/chat.png'));
    const uploadResponse = await uploaded;
    assert.equal((await uploadResponse.json()).code, 0, 'image upload accepted');
    await page.waitForFunction(() => [...document.querySelectorAll('img[src*="/api/media/private/"]')].some(img => img.complete && img.naturalWidth > 0), null, { timeout: 8000 }).catch(async () => {
      throw new Error('image rendering: ' + JSON.stringify(await page.locator('img').evaluateAll(rows => rows.filter(img => !img.src.startsWith('data:')).map(img => ({ src: img.src, naturalWidth: img.naturalWidth, complete: img.complete })))));
    });
    const staff = await fetch(`${input.base}/api/admin/chat-sessions/${input.sessionId}/messages/`, { headers: { Authorization: 'Bearer ' + input.staffToken } }).then(response => response.json());
    assert.equal(staff.code, 0); assert.ok(staff.data.list.some(row => row.content === '小屏验收咨询'));
    const imageMessage = staff.data.list.find(row => row.content_type === 'IMAGE' && row.content === '排班截图，麻烦核对' && row.image_url);
    assert.ok(imageMessage, 'staff can see provider image');
    assert.equal(imageMessage.content, '排班截图，麻烦核对');
    const imageResponse = await fetch(imageMessage.image_url);
    assert.equal(imageResponse.status, 200);
    assert.ok((await imageResponse.arrayBuffer()).byteLength > 0, 'staff can retrieve the actual image');
    await page.locator('textarea').fill('网络中断后的截图');
    const lostChooserPromise = page.waitForEvent('filechooser');
    await page.getByText('发送图片', { exact: true }).click();
    const lostChooser = await lostChooserPromise;
    dropNextSendResponse = true;
    await lostChooser.setFiles(path.resolve('apps/boss-miniapp/native/assets/icons/chat.png'));
    await Promise.race([responseDropped, new Promise((_, reject) => setTimeout(() => reject(new Error('upload response was not dropped')), 15000))]);
    await page.getByText('上条消息结果未确认，请先核对。', { exact: true }).waitFor({ timeout: 15000 });
    assert.equal(droppedResponse?.code, 0, 'the real server committed before the response was lost');
    await page.waitForFunction(() => [...document.querySelectorAll('[role="button"]')].some(button => button.textContent.trim() === '核对结果' && button.getAttribute('aria-disabled') === 'false'), null, { timeout: 20000 });
    dropNextSendResponse = false;
    await page.reload();
    await page.getByText('原图片已不可用，请先核对发送结果。', { exact: true }).waitFor({ timeout: 15000 });
    assert.equal(await page.getByText('重试原消息', { exact: true }).count(), 0);
    try { await page.getByText('核对结果', { exact: true }).click({ timeout: 8000 }); }
    catch (error) { throw new Error(`${error.message}\n${await page.locator('body').innerText()}`); }
    await page.getByText('上条消息结果未确认，请先核对。', { exact: true }).waitFor({ state: 'hidden' });
    await page.getByText('网络中断后的截图', { exact: true }).waitFor({ timeout: 15000 });
    const reply = await fetch(`${input.base}/api/admin/chat-sessions/${input.sessionId}/reply/`, { method: 'POST', headers: { Authorization: 'Bearer ' + input.staffToken, 'Content-Type': 'application/json' }, body: JSON.stringify({ content: '小屏客服回复' }) }).then(response => response.json());
    assert.equal(reply.code, 0); await page.reload();
    await page.getByText('小屏客服回复', { exact: true }).waitFor({ timeout: 15000 });
    await page.waitForFunction(() => {
      const text = [...document.querySelectorAll('*')].find(node => node.textContent === '小屏客服回复');
      const area = document.querySelector('taro-scroll-view-core');
      if (!text || !area) return false;
      const a = text.getBoundingClientRect(), b = area.getBoundingClientRect();
      return a.top >= b.top && a.bottom <= b.bottom;
    }, null, { timeout: 5000 });
    await page.locator('[aria-label="消息"]').waitFor({ timeout: 5000 });
    await page.waitForTimeout(650);
    await page.locator('taro-scroll-view-core').evaluate(node => { node.scrollTop = 0; });
    await page.getByText('回到最新消息', { exact: true }).waitFor({ timeout: 5000 });
    await page.getByText('回到最新消息', { exact: true }).click();
    await page.waitForFunction(() => {
      const text = [...document.querySelectorAll('*')].find(node => node.textContent === '小屏客服回复');
      const area = document.querySelector('taro-scroll-view-core');
      if (!text || !area) return false;
      const a = text.getBoundingClientRect(), b = area.getBoundingClientRect();
      return a.top >= b.top && a.bottom <= b.bottom;
    }, null, { timeout: 5000 });
    const folder = path.resolve('.tools/provider-chat-375-proof-20261101'); fs.mkdirSync(folder, { recursive: true });
    await page.screenshot({ path: path.join(folder, 'conversation.png') });
    console.log('375px provider H5: real message and staff reply visible');
  } finally { await browser.close(); server.close(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; server.close(); });
