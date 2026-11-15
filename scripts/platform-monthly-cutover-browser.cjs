const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless:true, args:['--no-proxy-server']})
  const context = await browser.newContext({viewport:{width:1366,height:1024}})
  const errors = []
  await context.addInitScript(user => sessionStorage.setItem('xa:fronted:platform:session:v1', JSON.stringify({
    token:user.token, refresh:user.refresh, sessionId:'platform-monthly-cutover',
    profile:{id:user.id, username:user.username, identity_type:'platform', grants:[{role:'SUPER_ADMIN', scope_slug:'*'}]},
  })), input)
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
    await page.goto('http://127.0.0.1:14103/#/')
    const section = page.locator('section').filter({has:page.getByRole('heading', {name:'门店管理', exact:true})})
    await section.getByRole('row').filter({hasText:'月券页面隔离店'}).getByRole('button', {name:'配置核对'}).click()
    const setup = page.getByRole('dialog').first()
    await setup.getByRole('button', {name:'核对切换规则'}).click()
    const confirm = page.getByRole('dialog', {name:'确认切换会员月券'})
    await confirm.getByText(/按已发 0 张处理/).waitFor()
    await confirm.getByRole('button', {name:'确认', exact:true}).click()
    await setup.getByText(/已切换：/).waitFor()
    assert.equal(await setup.getByRole('button', {name:'核对切换规则'}).count(), 0)
    assert.deepEqual(errors, [])
  } finally { await page.unrouteAll({behavior:'ignoreErrors'}); await context.close(); await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
