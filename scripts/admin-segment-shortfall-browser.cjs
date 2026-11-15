const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless:true, args:['--no-proxy-server']})
  const context = await browser.newContext({viewport:{width:1366,height:1024}})
  const errors = []
  await context.addInitScript(identity => {
    sessionStorage.setItem('xa:fronted:admin:active-store:v1', 'shenglang')
    localStorage.setItem('xa:store:shenglang:xa:fronted:admin:session:v1', JSON.stringify({token:identity.token,
      user:{id:identity.id, role:'staff', permissions:identity.permissions}, sessionId:'segment-shortfall'}))
  }, input.staff)
  const page = await context.newPage()
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
    await page.goto(`http://127.0.0.1/#/orders?detail=${input.orderId}`)
    const finance = page.locator('section').filter({has:page.getByRole('heading', {name:'接入时成交与抽成快照'})})
    await finance.getByText(/门店承担固定抽成差额 0\.1 .*尚未结算/).waitFor()
    await finance.getByText(/逐段结算待核对/).first().waitFor()
    assert.deepEqual(errors, [])
  } finally { await page.unrouteAll({behavior:'ignoreErrors'}); await context.close(); await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
