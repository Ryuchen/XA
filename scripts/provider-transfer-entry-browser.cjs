// Existing gateway frontend with real isolated ASGI and SQLite, no API mocks.
const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless:true, args:['--no-proxy-server']})
  const context = await browser.newContext({viewport:{width:390,height:844}, isMobile:true, hasTouch:true})
  await context.addInitScript(identity => localStorage.setItem('xa:store:shenglang:xa:fronted:provider:session:v1',
    JSON.stringify({token:identity.token, user:{id:identity.id, role:'provider', nickname:'隔离接替陪玩'}, sessionId:'transfer-entry-test'})), input.provider)
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
    const start = page.getByRole('button', {name:'确认转入入队', exact:true})
    await start.waitFor({timeout:15000})
    assert.equal(await page.getByRole('button', {name:'完成订单', exact:true}).count(), 0)
    await start.click()
    const dialog = page.getByRole('dialog', {name:'确认转入入队'})
    const confirm = dialog.getByRole('button', {name:'确认入队', exact:true})
    assert.equal(await confirm.isDisabled(), true)
    await dialog.locator('input[type=file]').setInputFiles({name:'entry.png', mimeType:'image/png',
      buffer:Buffer.from(input.image, 'base64')})
    await confirm.click()
    await dialog.waitFor({state:'hidden'})
    await page.getByText('本次入队已记录。', {exact:true}).waitFor()
    await page.getByRole('button', {name:'完成订单', exact:true}).waitFor()
    assert.equal(await start.count(), 0)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true)
    assert.deepEqual(errors, [])
  } catch (error) {
    console.error('page:', page.url(), 'text:', (await page.locator('body').innerText()).slice(0, 2500), 'errors:', errors)
    throw error
  } finally { await page.unrouteAll({behavior:'ignoreErrors'}); await context.close(); await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
