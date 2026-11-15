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
      user:{id:identity.id, role:'staff', permissions:identity.permissions}, sessionId:'admin-vip-threshold'}))
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
      if (!/already handled|Request context disposed|Target page, context or browser has been closed/i.test(String(error))) errors.push('isolated API proxy failed')
    }
  })
  try {
    await page.goto('http://127.0.0.1:14103/#/club-operations?tab=tiers')
    if (input.action === 'create') await page.getByRole('button', {name:'新增等级'}).click()
    else await page.getByRole('row').filter({hasText:'核对高级'}).getByRole('button', {name:'编辑权益'}).click()
    const dialog = page.getByRole('dialog', {name:'配置 VIP 升级权益'})
    if (input.action === 'create') {
      await dialog.locator('#field-vip-level').fill('3')
      await dialog.locator('#field-vip-threshold').fill('40')
      await dialog.getByRole('combobox', {name:'对应老板分级'}).click()
      await page.getByRole('option', {name:/核对新等级/}).click()
    } else if (input.action === 'configure') {
      await dialog.locator('#field-vip-monthly-count').fill('3')
      await dialog.locator('#field-vip-monthly-discount').fill('88')
      await dialog.locator('#field-vip-season-discount').fill('65')
    } else if (input.action === 'reactivate') await dialog.locator('#field-vip-active').click()
    else await dialog.locator('#field-vip-threshold').fill('50')
    if (input.action !== 'configure') await dialog.getByText(/将重算 1 位老板：升级 1 位、降级 0 位/).waitFor()
    await dialog.getByRole('button', {name:'确认', exact:true}).click()
    await dialog.waitFor({state:'hidden'})
    await page.getByText('VIP 权益已保存').waitFor()
    assert.deepEqual(errors, [])
  } finally { await page.unrouteAll({behavior:'ignoreErrors'}); await context.close(); await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
