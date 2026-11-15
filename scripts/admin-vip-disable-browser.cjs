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
      user:{id:identity.id, role:'staff', permissions:identity.permissions}, sessionId:'admin-vip-disable'}))
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
    await page.goto('http://127.0.0.1:14103/#/club-operations?tab=settings')
    const toggle = page.getByRole('switch', {name:'VIP 自动升级'})
    await toggle.waitFor()
    assert.equal(await toggle.getAttribute('data-state'), 'checked')
    await toggle.click()
    await page.getByRole('button', {name:'保存运营设置'}).click()
    const dialog = page.getByRole('dialog', {name:'保存运营设置'})
    await dialog.getByText(/未过期的券继续有效/).waitFor()
    await dialog.getByRole('button', {name:'确认', exact:true}).click()
    await dialog.waitFor({state:'hidden'})
    await page.getByText('运营设置已保存').waitFor()
    assert.deepEqual(errors, [])
  } finally { await page.unrouteAll({behavior:'ignoreErrors'}); await context.close(); await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
