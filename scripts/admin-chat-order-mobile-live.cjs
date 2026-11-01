// Real admin UI on a local isolated API; no business responses are replaced.
const fs = require('node:fs')
const path = require('node:path')
const net = require('node:net')
const assert = require('node:assert/strict')
const { spawn } = require('node:child_process')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
async function freePort() {
  const server = net.createServer()
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const port = server.address().port
  await new Promise(resolve => server.close(resolve))
  return port
}
;(async () => {
  const port = await freePort(), root = path.resolve('apps/admin-web')
  const vite = spawn(process.execPath, [path.join(root, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(port), '--strictPort'],
    { cwd: root, env: { ...process.env, XA_ADMIN_DEV_API_TARGET: input.api }, stdio: 'ignore', windowsHide: true })
  let browser
  try {
    const base = `http://127.0.0.1:${port}`
    for (let i = 0; i < 120; i++) {
      if (vite.exitCode !== null) throw new Error('Vite exited')
      try { if ((await fetch(base)).ok) break } catch {}
      await new Promise(resolve => setTimeout(resolve, 250))
      if (i === 119) throw new Error('Vite did not start')
    }
    browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true })
    const context = await browser.newContext({ viewport: { width: 375, height: 812 } })
    await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.token)
    const page = await context.newPage()
    const origins = new Set([base, input.api])
    await page.route('**/*', route => origins.has(new URL(route.request().url()).origin) ? route.continue() : route.abort())
    await page.goto(`${base}/chat`, { waitUntil: 'domcontentloaded' })
    await page.locator('.session-item').filter({ hasText: input.bossName }).click({ timeout: 30000 })
    const imageRow = page.locator('.msg-row').filter({ has: page.locator('.msg-image') }).first()
    await imageRow.locator('.image-caption').getByText(`咨询订单 ${input.orderNo}`, { exact: true }).waitFor({ timeout: 15000 })
    const link = imageRow.getByRole('button', { name: `查看订单 ${input.orderNo}` })
    await link.waitFor({ timeout: 15000 })
    await link.scrollIntoViewIfNeeded()
    const size = await link.boundingBox()
    assert.ok(size && size.x >= 0 && size.x + size.width <= 376, 'order shortcut remains within 375px viewport')
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, 'no horizontal overflow')
    const dir = path.resolve('.tools/admin-chat-order-375-20261031')
    fs.mkdirSync(dir, { recursive: true })
    await page.screenshot({ path: path.join(dir, 'chat.png') })
    await link.click()
    await page.getByText(`精确查找订单 ${input.orderNo}`, { exact: true }).waitFor({ timeout: 15000 })
    console.log('PASS real admin chat order navigation at 375px')
  } finally {
    if (browser) await browser.close()
    vite.kill()
  }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
