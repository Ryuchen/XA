// Real provider UI against disposable ASGI and SQLite.
const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless:true, args:['--no-proxy-server']})
  try {
    const context = await browser.newContext({viewport:{width:390,height:844}, isMobile:true, hasTouch:true})
    await context.addInitScript(({token,id}) => localStorage.setItem('xa:store:shenglang:xa:fronted:provider:session:v1', JSON.stringify({
      token, user:{id, role:'provider', nickname:'隔离测试陪玩'}, sessionId:'pass-points-test'
    })), {token:input.token, id:input.account})
    const page = await context.newPage(), errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/api/**', async route => {
      const target = new URL(route.request().url())
      const response = await route.fetch({url:input.api + target.pathname + target.search,
        headers:{...route.request().headers(), host:new URL(input.api).host}})
      await route.fulfill({response})
    })
    await page.goto('http://127.0.0.1:14102/player/#/pass')
    const bronze = page.locator('section').filter({has:page.getByRole('heading',{name:'铜卡通行证'})})
    await bronze.getByRole('button', {name:'采购1天'}).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByLabel('选择使用积分（选填）').fill('200')
    await dialog.getByRole('button', {name:'试算'}).click()
    await dialog.getByText(/实际使用 200 积分/).waitFor()
    await dialog.getByRole('button', {name:'确认采购'}).click()
    await page.getByText('采购成功', {exact:true}).waitFor({timeout:10000})
    await page.getByText(/积分抵扣 200 积分/).waitFor()
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true)
    assert.deepEqual(errors, [])
    await page.unrouteAll({behavior:'ignoreErrors'})
    await context.close()
  } finally { await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
