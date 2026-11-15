const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    headless:true, args:['--no-proxy-server']})
  const context = await browser.newContext({viewport:{width:390,height:844}, isMobile:true, hasTouch:true})
  const errors = []
  await context.addInitScript(user => localStorage.setItem('xa:store:shenglang:xa:fronted:boss:session:v1',
    JSON.stringify({token:user.token, user:{id:user.account, role:'boss', nickname:'隔离老板'}, sessionId:'reservation-funding-browser'})), input)
  const page = await context.newPage()
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/api/**', async route => {
    try {
      const target = new URL(route.request().url())
      const response = await route.fetch({url:input.api + target.pathname + target.search,
        headers:{...route.request().headers(), host:'sl.esports.club'}})
      await route.fulfill({response})
    } catch (error) {
      if (!/already handled|Request context disposed|Target page, context or browser has been closed/i.test(String(error)))
        errors.push('isolated API proxy failed')
    }
  })
  try {
    await page.goto(`http://127.0.0.1:14101/boss/#/reservation-detail?id=${input.reservation}`)
    await page.getByRole('button', {name:'获取最新报价'}).click()
    await page.getByLabel('使用赠送（浪花）').fill('20')
    await page.getByLabel('使用赔付（浪花）').fill('10')
    await page.getByRole('button', {name:'获取最新报价'}).click()
    await page.getByRole('button', {name:'确认成单', exact:true}).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByText('其中本金 70、赠送 20、赔付 10 浪花。', {exact:false}).waitFor()
    await dialog.getByRole('button', {name:'确认成单'}).click()
    await page.waitForURL(/order-detail\?id=/)
    assert.deepEqual(errors, [])
  } finally { await page.unrouteAll({behavior:'ignoreErrors'}); await context.close(); await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
