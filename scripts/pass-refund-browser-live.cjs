// Real provider page, actual isolated ASGI and SQLite, no financial mocks.
const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless:true})
  try {
    const context = await browser.newContext({viewport:{width:390,height:844}, isMobile:true, hasTouch:true})
    await context.addInitScript(({token,id}) => {
      localStorage.setItem('xa:store:shenglang:xa:fronted:provider:session:v1', JSON.stringify({
        token, user:{id, role:'provider', nickname:'隔离测试陪玩'}, sessionId:'pass-refund-test'
      }))
    }, {token:input.token, id:input.account})
    const page = await context.newPage(), errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/api/**', async route => {
      const target = new URL(route.request().url())
      const response = await route.fetch({url:input.api + target.pathname + target.search,
        headers:{...route.request().headers(), host:new URL(input.api).host}})
      await route.fulfill({response})
    })
    await page.goto('http://127.0.0.1:14102/player/#/pass')
    await page.getByRole('heading', {name:'接单通行证'}).waitFor()
    await page.getByRole('button', {name:'申请退款'}).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByText('退还待生效通行证').waitFor()
    await dialog.getByRole('button', {name:'确认退款'}).click()
    await page.getByText('通行证退款成功', {exact:true}).waitFor({timeout:10000})
    await page.getByText('已退款', {exact:true}).waitFor()
    assert.equal(await page.getByRole('button', {name:'申请退款'}).count(), 0)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true)
    assert.deepEqual(errors, [])
    process.stdout.write('provider browser refund passed\n')
  } finally { await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
