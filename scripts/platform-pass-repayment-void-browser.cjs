const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] })
  try {
    const context = await browser.newContext({ viewport: { width: 1366, height: 1024 } })
    await context.addInitScript(user => sessionStorage.setItem('xa:fronted:platform:session:v1', JSON.stringify({
      token: user.token, refresh: user.refresh, sessionId: 'finance-repayment-void-test',
      profile: { id: user.id, username: user.username, identity_type: 'platform',
        grants: [{ role: 'FINANCE', scope_slug: 'shenglang' }], capabilities: ['PLATFORM_PASS_REPAYMENT_V1'] },
    })), input)
    const page = await context.newPage(), errors = [], responses = []
    page.on('pageerror', error => errors.push(error.message))
    page.on('dialog', dialog => dialog.type() === 'prompt' ? dialog.accept('已核对门店无记录') : dialog.accept())
    page.on('response', response => { if (new URL(response.url()).pathname.startsWith('/api/')) responses.push(`${new URL(response.url()).pathname}: ${response.status()}`) })
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const target = new URL(route.request().url())
      try {
        const response = await route.fetch({ url: input.api + target.pathname + target.search,
          headers: { ...route.request().headers(), host: 'admin.esports.club' } })
        await route.fulfill({ response })
      } catch { try { await route.abort('failed') } catch { /* page closed */ } }
    })
    try {
      await page.goto('http://127.0.0.1:14103/#/')
      const outer = page.locator('section').filter({ has: page.getByRole('heading', { name: '通行证退款待补资金' }) })
      await outer.getByRole('button', { name: '查看明细' }).click({ timeout: 12000 })
      const section = page.locator('section').filter({ has: page.getByRole('heading', { name: '线下补款核对' }) })
      await section.getByRole('button', { name: '作废登记' }).click()
      await section.getByText('待确认操作已作废，原因已留痕。', { exact: true }).waitFor()
      await section.getByText('已作废：已核对门店无记录', { exact: true }).waitFor()
      assert.equal(await section.getByRole('button', { name: '作废登记' }).count(), 0)
      assert.deepEqual(errors, [])
    } catch (error) {
      console.error('page:', (await page.locator('body').innerText()).slice(0, 3500), responses, errors)
      throw error
    }
    await page.unrouteAll({ behavior: 'ignoreErrors' })
    await context.close()
  } finally { await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
