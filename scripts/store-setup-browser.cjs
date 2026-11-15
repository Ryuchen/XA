// Real admin app and isolated setup-only ASGI session.
const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    headless: true, args: ['--no-proxy-server'] })
  try {
    const context = await browser.newContext({ viewport: { width: 1366, height: 1024 } })
    await context.addInitScript(user => {
      sessionStorage.setItem('xa:fronted:admin:active-store:v1', 'shenglang')
      localStorage.setItem('xa:store:shenglang:xa:fronted:admin:session:v1', JSON.stringify({
        token: user.token, user: { id: user.id, role: 'staff', permissions: user.permissions }, sessionId: 'setup-browser',
      }))
    }, input)
    const page = await context.newPage(), errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const target = new URL(route.request().url())
      const response = await route.fetch({ url: input.api + target.pathname + target.search,
        headers: { ...route.request().headers(), host: 'admin.esports.club' } })
      await route.fulfill({ response })
    })
    await page.goto('http://127.0.0.1:14103/#/dictionaries')
    await page.getByText('门店尚未营业。当前仅开放首位管理员的初始化配置', { exact: false }).waitFor()
    const card = page.locator('[data-slot="card"]').filter({ has: page.getByRole('heading', { name: '门店核心配置核对' }) })
    const capacity = card.getByRole('row').filter({ hasText: '陪玩接单名额' })
    await capacity.getByText('未保存', { exact: true }).waitFor()
    await capacity.getByRole('button', { name: '打开配置' }).click()
    let dialog = page.getByRole('dialog').first()
    await dialog.getByRole('button', { name: '保存名额' }).click()
    await page.getByRole('dialog').last().getByRole('button', { name: '确认', exact: true }).click()
    await page.getByText('接单名额已保存').waitFor()
    await page.keyboard.press('Escape')
    await capacity.getByText('已保存', { exact: true }).waitFor()
    const code = await page.evaluate(async token => (await fetch('/api/admin/wallets/', {
      headers: { Authorization: `Bearer ${token}` },
    })).status, input.token)
    assert.equal(code, 403)
    assert.deepEqual(errors, [])
    await context.close()
  } finally { await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
