const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] })
  try {
    const context = await browser.newContext({ viewport: { width: 1366, height: 1024 } })
    await context.addInitScript(user => sessionStorage.setItem('xa:fronted:platform:session:v1', JSON.stringify({
      token: user.token, refresh: user.refresh, sessionId: 'finance-repayment-test',
      profile: { id: user.id, username: user.username, identity_type: 'platform',
        grants: [{ role: 'FINANCE', scope_slug: 'shenglang' }], capabilities: ['PLATFORM_PASS_REPAYMENT_V1'] },
    })), input)
    const page = await context.newPage(), errors = [], responses = []
    page.on('pageerror', error => errors.push(error.message))
    page.on('response', response => { if (new URL(response.url()).pathname.startsWith('/api/')) responses.push(`${new URL(response.url()).pathname}: ${response.status()}`) })
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const target = new URL(route.request().url())
      const response = await route.fetch({ url: input.api + target.pathname + target.search,
        headers: { ...route.request().headers(), host: 'admin.esports.club' } })
      await route.fulfill({ response })
    })
    await page.goto('http://127.0.0.1:14103/#/')
    const section = page.locator('section').filter({ has: page.getByRole('heading', { name: '通行证退款待补资金' }) })
    await section.getByRole('button', { name: '查看明细' }).click({ timeout: 12000 })
    const reconciliation = page.locator('section').filter({ has: page.getByRole('heading', { name: '线下补款核对' }) })
    try { await reconciliation.getByText('BROWSER-123', { exact: true }).waitFor({timeout: 12000}) }
    catch (error) { console.error('page:', (await page.locator('body').innerText()).slice(0, 2500), responses, errors); throw error }
    const receipt = reconciliation.getByRole('row').filter({ hasText: 'BROWSER-123' })
    const refund = reconciliation.getByRole('row').filter({ hasText: String(input.refund_id) }).filter({ hasText: '30 浪花' })
    await receipt.getByText('¥5.00', { exact: true }).waitFor()
    if (input.pending) {
      await reconciliation.getByText('存在结果待确认的操作，暂不显示剩余金额。', { exact: true }).waitFor()
      assert.equal(await receipt.getByText('待核对', { exact: true }).count(), 2)
      assert.equal(await refund.getByText('待核对', { exact: true }).count(), 2)
    } else {
      await receipt.getByText('20 浪花', { exact: true }).waitFor()
      await receipt.getByText('30 浪花', { exact: true }).waitFor()
      await refund.getByText('10 浪花', { exact: true }).waitFor()
    }
    assert.deepEqual(errors, [])
    await page.unrouteAll({ behavior: 'ignoreErrors' })
    await context.close()
  } finally { await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
