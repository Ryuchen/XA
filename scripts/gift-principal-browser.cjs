// Real boss gift page backed by isolated ASGI and temporary SQLite.
const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({ executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    headless:true, args:['--no-proxy-server'] })
  const errors = []
  async function open(identity) {
    const context = await browser.newContext({ viewport: {width:390,height:844}, isMobile:true, hasTouch:true })
    await context.addInitScript(user => localStorage.setItem('xa:store:shenglang:xa:fronted:boss:session:v1',
      JSON.stringify({token:user.token, user:{id:user.id, role:'boss', nickname:'隔离老板'}, sessionId:'gift-principal-browser'})), identity)
    const page = await context.newPage()
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/api/**', async route => {
      try {
        const target = new URL(route.request().url())
        const response = await route.fetch({ url:input.api + target.pathname + target.search,
          headers:{...route.request().headers(), host:'sl.esports.club'} })
        await route.fulfill({ response })
      } catch (error) {
        if (!/already handled|Request context disposed|Target page, context or browser has been closed/i.test(String(error)))
          errors.push('isolated API proxy failed')
      }
    })
    await page.goto(`http://127.0.0.1:14101/boss/#/gifts?provider_id=${input.recipient}`)
    await page.getByRole('button', {name:'添加隔离本金礼物'}).click()
    return {context, page}
  }
  try {
    const insufficient = await open(input.insufficient)
    await insufficient.page.getByText('本金余额不足，礼物只能使用本金支付。已选礼物会保留。').waitFor()
    assert.equal(await insufficient.page.getByRole('button', {name:'送给 TA'}).isDisabled(), true)
    await insufficient.page.unrouteAll({behavior:'ignoreErrors'})
    await insufficient.context.close()
    const sufficient = await open(input.sufficient)
    await sufficient.page.getByRole('button', {name:'送给 TA'}).click()
    const dialog = sufficient.page.getByRole('dialog')
    await dialog.getByText('共 1 件，将从本金余额扣除', {exact:false}).waitFor()
    await dialog.getByRole('button', {name:'确认赠送'}).click()
    await sufficient.page.getByText('心意已送达', {exact:true}).waitFor()
    await sufficient.page.unrouteAll({behavior:'ignoreErrors'})
    await sufficient.context.close()
    assert.deepEqual(errors, [])
  } finally { await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
