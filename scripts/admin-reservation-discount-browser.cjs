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
      user:{id:identity.id, role:'staff', permissions:identity.permissions}, sessionId:'reservation-discount-admin'}))
  }, input.staff)
  const page = await context.newPage()
  page.on('pageerror', error => errors.push(error.message))
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const target = new URL(route.request().url())
    const response = await route.fetch({url:input.api + target.pathname + target.search, headers:{...route.request().headers(), host:'admin.esports.club'}})
    await route.fulfill({response})
  })
  try {
    await page.goto('http://127.0.0.1:14103/#/reservations')
    await page.locator('article').filter({hasText:input.reservation_no}).getByRole('button', {name:'查看详情'}).click()
    await page.getByRole('dialog', {name:/预约详情/}).getByRole('button', {name:'报价并转单'}).click()
    const dialog = page.getByRole('dialog', {name:'确认预约转单'})
    const choice = dialog.getByLabel('优惠组合')
    await choice.selectOption({label:'隔离预约九折 · 实付 900 浪花'})
    await dialog.getByRole('button', {name:'重新核对报价'}).click()
    await choice.selectOption({label:'隔离预约八五折 + 隔离预约减300 · 实付 550 浪花（推荐）'})
    await dialog.getByRole('button', {name:'重新核对报价'}).click()
    await dialog.getByLabel('老板确认记录').fill('老板确认活动加金额券')
    await dialog.getByRole('checkbox').check()
    await dialog.getByRole('button', {name:'确认扣款并转单'}).click()
    await dialog.waitFor({state:'hidden'})
    assert.deepEqual(errors, [])
  } finally { await page.unrouteAll({behavior:'ignoreErrors'}); await context.close(); await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
