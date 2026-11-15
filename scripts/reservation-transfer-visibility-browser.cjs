const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless:true, args:['--no-proxy-server']})
  const contexts = [], errors = []
  async function open(role, identity, url) {
    const desktop = role === 'admin'
    const context = await browser.newContext({viewport:desktop ? {width:1366,height:1024} : {width:390,height:844}, hasTouch:!desktop})
    contexts.push(context)
    await context.addInitScript(({role, identity}) => {
      if (role === 'admin') sessionStorage.setItem('xa:fronted:admin:active-store:v1', 'shenglang')
      localStorage.setItem(`xa:store:shenglang:xa:fronted:${role}:session:v1`, JSON.stringify({token:identity.token,
        user:{id:identity.id, role, nickname:'隔离测试', permissions:identity.permissions || []}, sessionId:'reservation-transfer-visibility'}))
    }, {role, identity})
    const page = await context.newPage()
    page.on('pageerror', error => errors.push(error.message))
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const target = new URL(route.request().url())
      const response = await route.fetch({url:input.api + target.pathname + target.search,
        headers:{...route.request().headers(), host:role === 'admin' ? 'admin.esports.club' : 'sl.esports.club'}})
      await route.fulfill({response})
    })
    await page.goto(url)
    return page
  }
  try {
    const boss = await open('boss', input.boss, `http://127.0.0.1:14101/boss/#/reservation-detail?id=${input.booking}`)
    await boss.getByText('原预约：原陪玩验证').waitFor()
    await boss.getByText('当前履约：接替陪玩验证').waitFor()
    const provider = await open('provider', input.provider, `http://127.0.0.1:14102/player/#/reservations?id=${input.booking}`)
    await provider.getByText(input.bookingNo, {exact:true}).waitFor()
    const admin = await open('admin', input.staff, 'http://127.0.0.1:14103/#/reservations')
    const card = admin.locator('.admin-reservation-card').filter({hasText:input.bookingNo})
    await card.getByText('原预约：原陪玩验证').waitFor()
    await card.getByText('当前履约：接替陪玩验证').waitFor()
    assert.deepEqual(errors, [])
  } finally { for (const context of contexts) await context.close(); await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
