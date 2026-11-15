// Isolated admin UI and ASGI/SQLite: no external users or messages.
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
          sessionId: 'points-usage-test',
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
      const toggle = page.getByRole('switch', { name: '积分抽奖' })
      try { await toggle.waitFor({ timeout: 10000 }) }
      catch (error) { console.error('isolated page:', page.url(), (await page.locator('body').innerText()).slice(0, 1800), errors); throw error }
      if (!identity.canEdit) {
        assert.equal(await toggle.isDisabled(), true)
        assert.equal(await page.getByRole('button', { name: '保存开关' }).count(), 0)
      } else {
        await toggle.click()
        await page.getByRole('button', { name: '保存开关' }).click()
        const dialog = page.getByRole('dialog')
        await dialog.getByRole('heading', { name: '确认积分使用开关' }).waitFor()
        await dialog.getByRole('button', { name: '确认', exact: true }).click()
        await dialog.waitFor({ state: 'hidden' })
        await page.getByText('当前积分使用：暂停；积分抵扣付款尚未开放。').waitFor()
        assert.equal(await toggle.getAttribute('data-state'), 'unchecked')
      }
      assert.deepEqual(errors, [])
      await page.unrouteAll({ behavior: 'ignoreErrors' })
      await context.close()
    }
    process.stdout.write('points usage browser passed\n')
  } finally { await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
