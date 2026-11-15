const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless:true, args:['--no-proxy-server']})
  const errors = []
  async function contextFor(role, identity, host, viewport) {
    const context = await browser.newContext({viewport})
    await context.addInitScript(({role, identity}) => {
      if (role === 'admin') sessionStorage.setItem('xa:fronted:admin:active-store:v1', 'shenglang')
      localStorage.setItem(`xa:store:shenglang:xa:fronted:${role}:session:v1`, JSON.stringify({token:identity.token,
        user:{id:identity.id, role:role === 'admin' ? 'staff' : 'boss', nickname:'隔离测试', permissions:identity.permissions || []}, sessionId:'account-fields-browser'}))
    }, {role, identity})
    const page = await context.newPage()
    let apiQueue = Promise.resolve()
    page.on('pageerror', error => errors.push(error.message))
    page.on('requestfailed', request => { if (!request.url().includes('/api/')) errors.push(`resource failed ${request.url()}: ${request.failure()?.errorText}`) })
    page.on('response', response => { if (!response.url().includes('/api/') && response.status() >= 400) errors.push(`resource ${response.status()} ${response.url()}`) })
    page.on('response', response => {
      const path = new URL(response.url()).pathname
      // A disabled discount preview intentionally returns 404 and the checkout falls back.
      if (path.startsWith('/api/') && response.status() >= 400 && !(path === '/api/orders/discount-options-preview/' && response.status() === 404))
        errors.push(`API ${response.status()} ${path}`)
    })
    await page.route(url => url.pathname.startsWith('/api/'), route => {
      // SQLite's test database serializes writers; leave production requests untouched.
      const run = async () => {
      try {
        const target = new URL(route.request().url())
        const response = await route.fetch({url:input.api + target.pathname + target.search,
          headers:{...route.request().headers(), host}})
        await route.fulfill({response})
      } catch (error) {
        if (!/already handled|Request context disposed|Target page, context or browser has been closed/i.test(String(error)))
          errors.push('isolated API proxy failed')
      }
      }
      apiQueue = apiQueue.then(run, run)
      return apiQueue
    })
    return {context, page}
  }
  const admin = await contextFor('admin', input.staff, 'admin.esports.club', {width:1366,height:1024})
  const boss = await contextFor('boss', input.boss, 'sl.esports.club', {width:390,height:844})
  try {
    await admin.page.goto('http://127.0.0.1:14103/#/dictionaries')
    try { await admin.page.getByRole('button', {name:'下单游戏资料'}).waitFor({timeout:12000}) }
    catch (error) { console.error('隔离后台页面:', admin.page.url(), (await admin.page.locator('body').innerText()).slice(0, 1200), errors); throw error }
    await admin.page.getByRole('button', {name:'下单游戏资料'}).click()
    const dialog = admin.page.getByRole('dialog', {name:'下单游戏资料'})
    await dialog.getByRole('combobox', {name:'配置范围'}).click()
    await admin.page.getByRole('option', {name:'服务', exact:true}).click()
    await dialog.getByRole('combobox', {name:'选择服务'}).click()
    await admin.page.getByRole('option').filter({hasText:'隔离资料配置服务'}).click()
    await dialog.getByLabel('继承所属游戏的规则').uncheck()
    await dialog.getByLabel('游戏 UID').check()
    await dialog.getByRole('button', {name:'保存规则'}).click()
    await admin.page.getByRole('dialog', {name:'确认游戏资料规则'}).getByRole('button', {name:'确认'}).click()
    await admin.page.getByText('资料规则已保存').waitFor()
    await admin.page.keyboard.press('Escape')
    const evidence = admin.page.getByRole('row').filter({hasText:'下单游戏资料规则'})
    await evidence.getByText(/已明确配置全部 1 项可售服务/).waitFor()

    await boss.page.goto(`http://127.0.0.1:14101/boss/#/checkout?service_id=${input.service}&provider_id=${input.provider}`)
    const region = boss.page.getByLabel('游戏大区')
    const uid = boss.page.getByLabel('游戏 UID')
    try { await uid.waitFor({timeout:12000}) }
    catch (error) { console.error('隔离老板页面:', boss.page.url(), (await boss.page.locator('body').innerText()).slice(0, 2000), errors); throw error }
    assert.equal(await region.getAttribute('required'), null)
    assert.notEqual(await uid.getAttribute('required'), null)
    await uid.fill('browser-uid')
    await boss.page.getByRole('button', {name:'核对并确认下单'}).click()
    await boss.page.getByRole('dialog').getByRole('button', {name:'确认支付'}).click()
    await boss.page.waitForURL(/order-detail\?id=/)

    await admin.page.goto('http://127.0.0.1:14103/#/reservations')
    await admin.page.getByRole('button', {name:'代老板预约'}).click()
    const reservation = admin.page.getByRole('dialog', {name:'代老板预约'})
    await reservation.getByRole('combobox', {name:'服务套餐'}).click()
    await admin.page.getByRole('option').filter({hasText:'隔离资料配置服务'}).click()
    await reservation.getByLabel('游戏 UID').waitFor()
    await reservation.getByLabel('游戏 UID').waitFor({state:'visible'})
    await admin.page.waitForFunction(() => document.querySelector('#field-game_uid')?.hasAttribute('required'))
    assert.notEqual(await reservation.getByLabel('游戏 UID').getAttribute('required'), null)
    assert.equal(await reservation.getByLabel('游戏区服').getAttribute('required'), null)
    assert.deepEqual(errors, [])
  } finally {
    await admin.page.unrouteAll({behavior:'ignoreErrors'}); await boss.page.unrouteAll({behavior:'ignoreErrors'})
    await admin.context.close(); await boss.context.close(); await browser.close()
  }
})().catch(error => {console.error(error.stack || error); process.exitCode = 1})
