// Platform UI and actual scoped API, isolated ASGI/SQLite; all money is synthetic.
const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    headless: true, args: ['--no-proxy-server'] })
  try {
    const context = await browser.newContext({ viewport: { width: 1366, height: 1024 } })
    await context.addInitScript(session => sessionStorage.setItem('xa:fronted:platform:session:v1', JSON.stringify(session)), input.session)
    const page = await context.newPage(), errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const target = new URL(route.request().url())
      const response = await route.fetch({ url: input.api + target.pathname + target.search,
        headers: { ...route.request().headers(), host: 'admin.esports.club' } })
      await route.fulfill({ response })
    })
    await page.goto('http://127.0.0.1:14103/#/')
    const section = page.locator('section[aria-labelledby="platform-withdraw-title"]')
    await section.getByRole('heading', { name: '平台提现审核' }).waitFor()
    await section.getByRole('button', { name: /声浪电竞.*1 历史待核实/ }).click()
    await section.getByRole('button', { name: '历史待核实', exact: true }).click()
    const row = section.getByRole('row').filter({ hasText: '隔离历史申请' })
    await row.getByText('历史申请待核实').waitFor()
    await row.getByText(/钱包冻结总额/).waitFor()
    await row.getByText(/账户信息匹配/).waitFor()
    await section.getByText(/不能证明是否已线下打款/).waitFor()
    assert.equal(await row.getByRole('button').count(), 0)
    assert.deepEqual(errors, [])
    process.stdout.write('platform legacy withdrawal inspection browser passed\n')
  } finally { await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
