// Real admin UI and isolated ASGI; only synthetic accounts and a temporary receipt.
const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    headless: true, args: ['--no-proxy-server'] })
  const errors = []
  try {
    const context = await browser.newContext({ viewport: { width: 1366, height: 1024 } })
    await context.addInitScript(user => {
      sessionStorage.setItem('xa:fronted:admin:active-store:v1', 'shenglang')
      localStorage.setItem('xa:store:shenglang:xa:fronted:admin:session:v1', JSON.stringify({ token: user.token,
        user: { id: user.id, role: 'staff', permissions: user.permissions }, sessionId: 'recharge-pricing-browser' }))
    }, input)
    const page = await context.newPage()
    page.on('pageerror', error => errors.push(error.message))
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      try {
        const target = new URL(route.request().url())
        const response = await route.fetch({ url: input.api + target.pathname + target.search,
          headers: { ...route.request().headers(), host: 'admin.esports.club' } })
        await route.fulfill({ response })
      } catch (error) {
        if (!/already handled|Request context disposed|Target page, context or browser has been closed/i.test(String(error)))
          errors.push('isolated API proxy failed')
      }
    })
    await page.goto('http://127.0.0.1:14103/#/wallets')
    const row = page.getByRole('row').filter({ hasText: 'synthetic-recharge-browser' })
    await row.getByRole('button', { name: '充值', exact: true }).click()
    const dialog = page.getByRole('dialog').last()
    await dialog.getByLabel('实收人民币（元）').fill('9000')
    await dialog.getByLabel('赠送浪花').waitFor()
    await page.waitForFunction(() => document.querySelector('#wallet-gift')?.value === '3500')
    await dialog.getByLabel('赠送浪花').fill('4000')
    await dialog.getByLabel('收款流水号').fill('synthetic-browser-receipt')
    await dialog.locator('#recharge-proof').setInputFiles({ name: 'receipt.png', mimeType: 'image/png',
      buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAE0lEQVR4nGP8//8/AwMDEwMYAAAkBgMBXaJOiAAAAABJRU5ErkJggg==', 'base64') })
    await dialog.getByRole('checkbox').check()
    await dialog.getByRole('button', { name: '确认入账' }).click()
    await dialog.waitFor({ state: 'hidden' })
    await page.goto('http://127.0.0.1:14103/#/recharge-records')
    const record = page.getByRole('row').filter({ hasText: 'synthetic-recharge-browser' })
    await record.getByText('赠送审批：待审批').waitFor()
    await record.getByRole('button', { name: '通过', exact: true }).click()
    await page.getByRole('dialog').last().getByRole('button', { name: '确认', exact: true }).click()
    await record.getByText('赠送审批：已通过').waitFor()
    await page.unrouteAll({ behavior: 'ignoreErrors' })
    assert.deepEqual(errors, [])
    await context.close()
  } finally { await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
