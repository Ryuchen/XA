// Real three-role workflow on a disposable ASGI/SQLite database. No external payout.
const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))

function localTime(secondsAgo) {
  const d = new Date(Date.now() - secondsAgo * 1000), part = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${part(d.getMonth()+1)}-${part(d.getDate())}T${part(d.getHours())}:${part(d.getMinutes())}:${part(d.getSeconds())}`
}
;(async () => {
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    headless: true, args: ['--no-proxy-server'] })
  try {
    const context = await browser.newContext({ viewport: { width: 1366, height: 1024 } })
    await context.addInitScript(session => sessionStorage.setItem('xa:fronted:platform:session:v1', JSON.stringify(session)), input.session)
    const page = await context.newPage(), errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const target = new URL(route.request().url())
      const response = await route.fetch({ url: input.api + target.pathname + target.search,
        headers: { ...route.request().headers(), host: 'admin.esports.club' } })
      await route.fulfill({ response })
    })
    await page.goto('http://127.0.0.1:14103/#/')
    const section = page.locator('section[aria-labelledby="platform-withdraw-title"]')
    await section.getByRole('button', { name: /声浪电竞/ }).click()
    await section.getByText('暂无记录').waitFor()
    async function staffPost(path, data) {
      const response = await context.request.post(input.api + path, { headers: { host: 'admin.esports.club', Authorization: `Bearer ${input.staffToken}` }, data })
      const body = await response.json()
      assert.equal(body.code, 0, JSON.stringify(body))
      return body.data
    }
    const claim = await staffPost(`/api/admin/withdrawals/${input.id}/claim/`, {})
    await staffPost(`/api/admin/withdrawals/${input.id}/approve/`, { expected_claimed_at: claim.claimed_at })
    const row = section.getByRole('row').filter({ hasText: '隔离三端提现' })
    // No user refresh: the platform list must discover the initial review itself.
    await row.getByRole('button', { name: '终审通过' }).waitFor({ timeout: 22000 })
    await row.getByRole('button', { name: '终审通过' }).click()
    let dialog = section.getByRole('dialog')
    await dialog.getByLabel('已核对该申请').check()
    await dialog.getByRole('button', { name: '确认', exact: true }).click()
    await row.getByRole('button', { name: '登记打款' }).click()
    dialog = section.getByRole('dialog')
    await dialog.getByLabel('打款凭据编号').fill('QA-THREE-ROLE-PAYOUT')
    await dialog.getByLabel('实际打款时间').fill(localTime(2))
    await dialog.getByLabel('已核对实际打款与凭据').check()
    await dialog.getByRole('button', { name: '确认', exact: true }).click()
    await section.getByRole('button', { name: '处理记录' }).click()
    await section.getByRole('row').filter({ hasText: '隔离三端提现' }).getByText('已打款').waitFor()
    assert.deepEqual(errors, [])

    const providerContext = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true })
    await providerContext.addInitScript(identity => localStorage.setItem('xa:store:shenglang:xa:fronted:provider:session:v1',
      JSON.stringify({ token: identity.token, user: { id: identity.id, role: 'provider' }, sessionId: 'three-role-provider' })), input.provider)
    const providerPage = await providerContext.newPage()
    await providerPage.route(url => url.pathname.startsWith('/api/'), async route => {
      const target = new URL(route.request().url())
      const response = await route.fetch({ url: input.api + target.pathname + target.search,
        headers: { ...route.request().headers(), host: 'sl.esports.club' } })
      await route.fulfill({ response })
    })
    // This scenario validates durable notifications over HTTP, not an external socket.
    await providerPage.addInitScript(() => { window.WebSocket = class { constructor() { throw new Error('isolated socket disabled') } } })
    await providerPage.goto('http://127.0.0.1:14102/player/#/messages')
    await providerPage.getByRole('button', { name: /提现已登记打款/ }).click()
    await providerPage.getByRole('button', { name: '查看相关内容' }).click()
    await providerPage.waitForURL(url => url.hash.includes(`withdraw_id=${input.id}`))
    await providerPage.getByText(`申请 #${input.id}`, { exact: false }).waitFor()
    assert.equal(await providerPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true)
    await providerContext.close()
    await context.close()
    process.stdout.write('three-role withdrawal browser passed\n')
  } finally { await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
