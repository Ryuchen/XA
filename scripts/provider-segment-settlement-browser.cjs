const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless:true, args:['--no-proxy-server']})
  const context = await browser.newContext({viewport:{width:390,height:844}, isMobile:true, hasTouch:true})
  await context.addInitScript(identity => localStorage.setItem('xa:store:shenglang:xa:fronted:provider:session:v1',
    JSON.stringify({token:identity.token, user:{id:identity.id, role:'provider', nickname:'隔离陪玩'}, sessionId:'segment-credit'})), input.provider)
  const page = await context.newPage(), errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route(url => url.hostname === '127.0.0.1' && !url.pathname.startsWith('/api/'), async route => {
    const response = await route.fetch({headers:{...route.request().headers(), host:'sl.esports.club'}})
    await route.fulfill({response})
  })
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const target = new URL(route.request().url())
    const response = await route.fetch({url:input.api + target.pathname + target.search,
      headers:{...route.request().headers(), host:'sl.esports.club'}})
    await route.fulfill({response})
  })
  try {
    await page.goto(`http://127.0.0.1/player/#/orders?order=${input.order}`)
    await page.getByText(/结算状态：个人份额已结算/).first().waitFor()
    await page.getByRole('button', {name:/查看订单 .* 详情/}).click()
    await page.getByRole('heading', {name:'我的履约记录'}).waitFor()
    await page.getByText('本次履约报单 · 审核通过').first().waitFor()
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true)
    assert.deepEqual(errors, [])
  } finally { await page.unrouteAll({behavior:'ignoreErrors'}); await context.close(); await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
