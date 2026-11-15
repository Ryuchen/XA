// Real admin UI against a disposable ASGI/SQLite backend.
const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))

;(async () => {
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true, args: ['--no-proxy-server'] })
  try {
    for (const identity of [input.viewer, input.editor]) {
      const context = await browser.newContext({ viewport: { width: 1366, height: 1024 } })
      await context.addInitScript(identity => {
        sessionStorage.setItem('xa:fronted:admin:active-store:v1', 'shenglang')
        localStorage.setItem('xa:store:shenglang:xa:fronted:admin:session:v1', JSON.stringify({
          token: identity.token, user: { id: identity.id, role: 'staff', permissions: identity.permissions },
          sessionId: 'points-redemption-config-test',
        }))
      }, identity)
      const page = await context.newPage(), errors = []
      page.on('pageerror', error => errors.push(error.message))
      await page.route(url => url.pathname.startsWith('/api/'), async route => {
        const target = new URL(route.request().url())
        const response = await route.fetch({ url: input.api + target.pathname + target.search,
          headers: { ...route.request().headers(), host: new URL(input.api).host } })
        await route.fulfill({ response })
      })
      await page.goto('http://127.0.0.1:14103/#/dictionaries')
      const points = page.getByLabel('每组积分')
      try { await points.waitFor({ timeout: 10000 }) }
      catch (error) { console.error('isolated page:', page.url(), (await page.locator('body').innerText()).slice(0, 1600), errors); throw error }
      if (!identity.canEdit) {
        assert.equal(await points.isDisabled(), true)
        assert.equal(await page.getByRole('button', { name: '保存规则' }).count(), 0)
      } else {
        await points.pressSequentially('12.34')
        assert.equal(await points.inputValue(), '12.34')
        await page.locator('#redemption-coins').fill('3')
        await page.locator('#redemption-cap').fill('45.55')
        await page.getByRole('button', { name: '保存规则' }).click()
        const dialog = page.getByRole('dialog')
        await dialog.getByRole('heading', { name: '确认积分抵扣规则' }).waitFor()
        await dialog.getByRole('button', { name: '确认', exact: true }).click()
        await dialog.waitFor({ state: 'hidden' })
        assert.equal(await points.inputValue(), '12.34')
      }
      assert.deepEqual(errors, [])
      await context.close()
    }
    process.stdout.write('points configuration browser passed\n')
  } finally { await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
