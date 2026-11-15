// Real admin Vite and isolated ASGI, using synthetic store accounts only.
const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    headless: true, args: ['--no-proxy-server'] })
  const errors = []
  async function open(identity) {
    const context = await browser.newContext({ viewport: { width: 1366, height: 1024 } })
    await context.addInitScript(user => {
      sessionStorage.setItem('xa:fronted:admin:active-store:v1', 'shenglang')
      localStorage.setItem('xa:store:shenglang:xa:fronted:admin:session:v1', JSON.stringify({
        token: user.token, user: { id: user.id, role: 'staff', permissions: user.permissions }, sessionId: 'setup-evidence-browser',
      }))
    }, identity)
    const page = await context.newPage()
    page.on('pageerror', error => errors.push(error.message))
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const target = new URL(route.request().url())
      const response = await route.fetch({ url: input.api + target.pathname + target.search,
        headers: { ...route.request().headers(), host: 'admin.esports.club' } })
      await route.fulfill({ response })
    })
    await page.goto('http://127.0.0.1:14103/#/dictionaries')
    await page.getByRole('heading', { name: '门店核心配置核对' }).waitFor()
    return { context, page }
  }
  try {
    const editor = await open(input.editor)
    const evidence = editor.page.locator('[data-slot="card"]').filter({ has: editor.page.getByRole('heading', { name: '门店核心配置核对' }) })
    const row = evidence.getByRole('row').filter({ hasText: '陪玩接单名额' })
    await row.getByText('未保存', { exact: true }).waitFor()
    await editor.page.getByText('仅检查下列已接入的配置', { exact: false }).waitFor()
    const saved = await editor.page.evaluate(async token => {
      const response = await fetch('/api/admin/config/provider-capacity/', { method: 'PUT',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ max_concurrent_orders: 4, version: 0 }) })
      return response.status
    }, input.editor.token)
    assert.equal(saved, 200)
    await editor.page.reload()
    await evidence.getByRole('row').filter({ hasText: '陪玩接单名额' }).getByText('已保存', { exact: true }).waitFor()
    const deposit = evidence.getByRole('row').filter({ hasText: '单抵押金比例' })
    await deposit.getByRole('button', { name: '核对并确认' }).click()
    const dialog = editor.page.getByRole('dialog')
    await dialog.getByText('0%', { exact: true }).waitFor()
    await dialog.getByRole('button', { name: '确认当前配置' }).click()
    await deposit.getByText('已确认', { exact: true }).waitFor()
    const reader = await open(input.reader)
    const readerEvidence = reader.page.locator('[data-slot="card"]').filter({ has: reader.page.getByRole('heading', { name: '门店核心配置核对' }) })
    const restricted = readerEvidence.getByRole('row').filter({ hasText: '提现规则' })
    await restricted.getByText('无查看权限', { exact: true }).waitFor()
    assert.equal(await restricted.getByRole('link').count(), 0)
    await reader.context.close()
    await editor.context.close()
    assert.deepEqual(errors, [])
    process.stdout.write('setup evidence browser passed\n')
  } finally { await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
