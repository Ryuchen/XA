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
      user:{id:identity.id, role:'staff', permissions:identity.permissions}, sessionId:'segment-evidence'}))
  }, input.staff)
  const page = await context.newPage()
  page.on('pageerror', error => errors.push(error.message))
  await page.route(url => url.hostname === '127.0.0.1' && !url.pathname.startsWith('/api/'), async route => {
    const response = await route.fetch({headers:{...route.request().headers(), host:'admin.esports.club'}})
    await route.fulfill({response})
  })
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const target = new URL(route.request().url())
    const response = await route.fetch({url:input.api + target.pathname + target.search,
      headers:{...route.request().headers(), host:'admin.esports.club'}})
    await route.fulfill({response})
  })
  try {
    await page.goto(`http://127.0.0.1/#/orders?detail=${input.order}`)
    const evidence = page.locator('section').filter({has:page.getByRole('heading', {name:'履约凭证'})})
    await evidence.getByRole('heading', {name:'履约凭证'}).waitFor()
    const images = evidence.getByRole('img', {name:'查看结单截图 1'})
    assert.equal(await images.count(), 2)
    await images.first().evaluate(img => img.decode())
    await images.last().evaluate(img => img.decode())
    if (input.segmentReports) {
      const reports = evidence.locator('[data-testid^="segment-report-review-"]')
      assert.equal(await reports.count(), 2)
      await reports.first().getByLabel('本次报单审核意见').fill('请补充战绩')
      await reports.first().getByRole('button', {name:'驳回报单'}).click()
      await reports.first().getByText(/已驳回/).first().waitFor()
      await reports.last().getByRole('button', {name:'审核通过'}).click()
      await reports.last().getByText(/审核通过/).first().waitFor()
      const plan = page.getByTestId('segment-settlement-plan')
      await plan.getByRole('button', {name:'核对逐段收益'}).click()
      await plan.getByText(/预计门店留存/).waitFor()
      assert.equal(await plan.getByText(/履约段 #/).count(), 3)
      assert.equal(await plan.getByText(/本人履约报单未全部通过审核/).count() > 0, true)
    }
    assert.deepEqual(errors, [])
  } finally { await page.unrouteAll({behavior:'ignoreErrors'}); await context.close(); await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
