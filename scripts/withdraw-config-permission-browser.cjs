// Real admin UI against a disposable ASGI/SQLite backend; no external traffic.
const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))

;(async () => {
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true, args: ['--no-proxy-server'] })
  try {
    for (const identity of [input.reviewer, input.editor]) {
      const context = await browser.newContext({ viewport: { width: 1366, height: 1024 } })
      await context.addInitScript(identity => {
        sessionStorage.setItem('xa:fronted:admin:active-store:v1', 'shenglang')
        localStorage.setItem('xa:store:shenglang:xa:fronted:admin:session:v1', JSON.stringify({
          token: identity.token, user: { id: identity.id, role: 'staff', permissions: identity.permissions },
          sessionId: 'withdraw-config-permission-test',
        }))
      }, identity)
      const page = await context.newPage(), errors = []
      page.on('pageerror', error => errors.push(error.message))
      page.on('requestfailed', request => { if (request.failure()?.errorText !== 'net::ERR_ABORTED') errors.push(`${request.url()} ${request.failure()?.errorText}`) })
      await page.route(url => url.pathname.startsWith('/api/'), async route => {
        const target = new URL(route.request().url())
        const response = await route.fetch({ url: input.api + target.pathname + target.search,
          headers: { ...route.request().headers(), host: new URL(input.api).host } })
        await route.fulfill({ response })
      })
      await page.goto('http://127.0.0.1:14103/#/withdrawals')
      try { await page.getByRole('heading', { name: '提现审核' }).waitFor({ timeout: 10000 }) }
      catch (error) { console.error('isolated page:', page.url(), (await page.locator('body').innerText()).slice(0, 1500), errors); throw error }
      const button = page.getByRole('button', { name: '提现规则' })
      if (identity.canEdit) {
        await button.click()
        await page.getByRole('dialog').getByText('提现规则', { exact: true }).first().waitFor()
      } else assert.equal(await button.count(), 0)
      assert.deepEqual(errors, [])
      await context.close()
    }
    process.stdout.write('withdraw configuration permission browser passed\n')
  } finally { await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
