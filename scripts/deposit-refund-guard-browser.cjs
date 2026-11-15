// Actual admin page backed by disposable ASGI/SQLite; no external communication.
const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    headless: true, args: ['--no-proxy-server'] })
  try {
    const context = await browser.newContext({ viewport: { width: 1366, height: 1024 } })
    await context.addInitScript(identity => {
      sessionStorage.setItem('xa:fronted:admin:active-store:v1', 'shenglang')
      localStorage.setItem('xa:store:shenglang:xa:fronted:admin:session:v1', JSON.stringify({
        token: identity.token, user: { id: identity.id, role: 'staff', permissions: identity.permissions },
        sessionId: 'deposit-guard-browser',
      }))
    }, input.staff)
    const page = await context.newPage(), errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const target = new URL(route.request().url())
      const response = await route.fetch({ url: input.api + target.pathname + target.search,
        headers: { ...route.request().headers(), host: 'admin.esports.club' } })
      await route.fulfill({ response })
    })
    await page.goto('http://127.0.0.1:14103/#/escorts')
    await page.getByRole('row').filter({ hasText: '隔离退押金陪玩' }).getByRole('button', { name: '查看档案' }).click()
    await page.getByRole('dialog').getByRole('button', { name: '退还押金' }).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByText('仍有 1 笔进行中的订单').waitFor()
    assert.equal(await dialog.locator('button[type="submit"]').isDisabled(), true)
    assert.deepEqual(errors, [])
    process.stdout.write('deposit refund guard browser passed\n')
  } finally { await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
