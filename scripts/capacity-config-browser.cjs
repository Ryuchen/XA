// Actual admin Vite page and isolated ASGI; no production state or messages.
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
        sessionId: 'capacity-browser',
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
    await page.goto('http://127.0.0.1:14103/#/orders')
    await page.getByRole('button', { name: '接单名额' }).click()
    const panel = page.getByRole('dialog').first()
    await panel.getByLabel('每位陪玩同时接单上限').fill('2')
    await panel.getByRole('button', { name: '保存名额' }).click()
    const confirm = page.getByRole('dialog').last()
    await confirm.getByText('本店每位陪玩的上限由 3 单调整为 2 单。').waitFor()
    await confirm.getByRole('button', { name: '确认', exact: true }).click()
    await panel.getByLabel('每位陪玩同时接单上限').waitFor()
    await page.getByText('接单名额已保存').waitFor()
    assert.equal(await panel.getByLabel('每位陪玩同时接单上限').inputValue(), '2')
    assert.deepEqual(errors, [])
    process.stdout.write('capacity config browser passed\n')
  } finally { await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
