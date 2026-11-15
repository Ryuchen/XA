const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))

;(async () => {
  const browser = await chromium.launch({ executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    headless:true, args:['--no-proxy-server'] })
  const context = await browser.newContext({viewport:{width:390,height:844}})
  await context.addInitScript(identity => {
    localStorage.setItem('xa:store:shenglang:xa:fronted:boss:session:v1', JSON.stringify({token:identity.token,
      user:{id:identity.id, role:'boss', nickname:'隔离测试'}, sessionId:'points-cancel-browser'}))
  }, input.boss)
  const page = await context.newPage()
  let markDropped
  const dropped = new Promise(resolve => { markDropped = resolve })
  let interrupted = false
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const url = new URL(route.request().url())
    const response = await route.fetch({url:input.api + url.pathname + url.search,
      headers:{...route.request().headers(), host:'sl.esports.club'}})
    if (!interrupted && route.request().method() === 'POST' && url.pathname.endsWith('/cancel/')) {
      interrupted = true
      await route.abort('failed')
      markDropped()
    } else await route.fulfill({response})
  })
  try {
    await page.goto(`http://127.0.0.1:14101/boss/#/order-detail?id=${input.order}`)
    await page.getByRole('button', {name:'取消订单'}).click()
    const dialog = page.getByRole('dialog', {name:'确认取消订单？'})
    await dialog.getByText(/使用 100 积分抵扣 3 浪花/).waitFor()
    await dialog.getByText(/店币和积分将分别退回/).waitFor()
    await dialog.getByRole('button', {name:'确认取消'}).click()
    await Promise.race([dropped, new Promise((_, reject) => setTimeout(() => reject(new Error('取消请求未完成')), 15000))])
    await page.reload()
    await page.getByText('订单已取消', {exact:true}).first().waitFor()
    assert.equal(await page.getByRole('button', {name:'取消订单'}).count(), 0)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true)
  } finally {
    await page.unrouteAll({behavior:'ignoreErrors'})
    await context.close()
    await browser.close()
  }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
