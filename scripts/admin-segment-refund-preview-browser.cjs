const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless:true, args:['--no-proxy-server']})
  const context = await browser.newContext({viewport:{width:1366,height:1024}})
  await context.addInitScript(identity => {
    sessionStorage.setItem('xa:fronted:admin:active-store:v1', 'shenglang')
    localStorage.setItem('xa:store:shenglang:xa:fronted:admin:session:v1', JSON.stringify({token:identity.token,
      user:{id:identity.id, role:'staff', permissions:identity.permissions}, sessionId:'segment-refund-preview'}))
  }, input.staff)
  const page = await context.newPage(), errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route(url => url.hostname === '127.0.0.1' && !url.pathname.startsWith('/api/'), async route => {
    const response = await route.fetch({headers:{...route.request().headers(), host:'admin.esports.club'}})
    await route.fulfill({response})
  })
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const target = new URL(route.request().url())
    const response = await route.fetch({url:input.api + target.pathname + target.search,
      headers:{...route.request().headers(), host:'admin.esports.club'}})
    await route.fulfill({response})
  })
  try {
    await page.goto(`http://127.0.0.1/#/orders?detail=${input.order}`)
    await page.getByRole('button', {name:'退款试算'}).click()
    await page.getByRole('heading', {name:'逐段退款分摊核对'}).waitFor()
    await page.getByText(/仅供核对；逐段退款/).first().waitFor()
    await page.getByLabel(/本次退款/).fill('51')
    await page.getByRole('button', {name:'读取最新试算'}).click()
    if (input.scopeLocked) {
      await page.getByText(/预计门店取整差额 0/).waitFor()
      await page.getByText(/受影响陪玩（按本次售后范围）/).waitFor()
    } else {
      await page.getByText(/预计门店取整差额 1/).waitFor()
      const scope = page.getByText('受影响陪玩', {exact:true}).locator('..')
      assert.equal(await scope.locator('input[type=checkbox]').count(), 2)
      await scope.locator('input[type=checkbox]').last().uncheck()
      await page.getByRole('button', {name:'读取最新试算'}).click()
      await page.getByText(/预计门店取整差额 0/).waitFor()
    }
    assert.equal(await page.getByRole('button', {name:'确认退款'}).isDisabled(), true)
    assert.deepEqual(errors, [])
  } finally { await page.unrouteAll({behavior:'ignoreErrors'}); await context.close(); await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
