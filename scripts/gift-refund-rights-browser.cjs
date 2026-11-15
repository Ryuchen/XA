// Actual boss page backed by disposable ASGI/SQLite; no business mocks.
const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    headless:true, args:['--no-proxy-server']})
  try {
    const context = await browser.newContext({viewport:{width:390,height:844}, isMobile:true, hasTouch:true})
    await context.addInitScript(({token, id}) => localStorage.setItem('xa:store:shenglang:xa:fronted:boss:session:v1',
      JSON.stringify({token, user:{id, role:'boss', nickname:'隔离老板'}, sessionId:'gift-rights-test'})),
      {token:input.token, id:input.account})
    const page = await context.newPage(), errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/api/**', async route => {
      const target = new URL(route.request().url())
      const response = await route.fetch({url:input.api + target.pathname + target.search,
        headers:{...route.request().headers(), host:new URL(input.api).host}})
      await route.fulfill({response})
    })
    await page.goto(`http://127.0.0.1:14101/boss/#/order-detail?id=${input.order}`)
    await page.getByText('如已获得赠礼权益，将继续保留；未交付权益继续交付', {exact:true}).waitFor({timeout:15000})
    assert.equal(await page.getByText('未交付，已撤销', {exact:true}).count(), 0)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true)
    assert.deepEqual(errors, [])
    process.stdout.write('boss gift refund rights browser passed\n')
  } finally { await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
