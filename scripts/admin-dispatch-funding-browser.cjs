const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    headless:true, args:['--no-proxy-server']})
  const context = await browser.newContext({viewport:{width:1366,height:1024}})
  const errors = []
  await context.addInitScript(identity => {
    sessionStorage.setItem('xa:fronted:admin:active-store:v1', 'shenglang')
    localStorage.setItem('xa:store:shenglang:xa:fronted:admin:session:v1', JSON.stringify({token:identity.token,
      user:{id:identity.id, role:'staff', permissions:identity.permissions}, sessionId:'admin-dispatch-funding'}))
  }, input.staff)
  const page = await context.newPage()
  page.on('pageerror', error => errors.push(error.message))
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    try {
      const target = new URL(route.request().url())
      const response = await route.fetch({url:input.api + target.pathname + target.search,
        headers:{...route.request().headers(), host:'admin.esports.club'}})
      await route.fulfill({response})
    } catch (error) {
      if (!/already handled|Request context disposed|Target page, context or browser has been closed/i.test(String(error)))
        errors.push('isolated API proxy failed')
    }
  })
  try {
    await page.goto('http://127.0.0.1:14103/#/orders')
    await page.getByRole('button', {name:'快捷派单'}).click()
    const dialog = page.getByRole('dialog', {name:'快捷派单'})
    await dialog.getByRole('combobox', {name:'下单老板'}).click()
    await page.getByRole('option').filter({hasText:'隔离代单老板'}).click()
    await dialog.getByRole('combobox', {name:'服务套餐'}).click()
    await page.getByRole('option').filter({hasText:'隔离代单余额服务'}).click()
    await dialog.getByRole('button', {name:'读取最新报价与抽成'}).click()
    await dialog.getByLabel('使用赠送（浪花）').fill('20')
    await dialog.getByLabel('使用赔付（浪花）').fill('10')
    await dialog.getByRole('button', {name:'读取最新报价与抽成'}).click()
    await dialog.getByLabel('老板确认记录').fill('老板确认赠送20、赔付10，其余本金')
    await dialog.getByRole('checkbox').check()
    await dialog.getByRole('button', {name:'确认扣款并派单'}).click()
    await dialog.waitFor({state:'hidden'})
    assert.deepEqual(errors, [])
  } finally { await page.unrouteAll({behavior:'ignoreErrors'}); await context.close(); await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
