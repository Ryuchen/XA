const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] })
  try {
    const context = await browser.newContext({ viewport: { width: 1366, height: 1024 } })
    await context.addInitScript(user => {
      if (!sessionStorage.getItem('xa:fronted:platform:session:v1')) sessionStorage.setItem('xa:fronted:platform:session:v1', JSON.stringify({
        token: user.token, refresh: user.refresh, sessionId: 'finance-repayment-action-test',
        profile: { id: user.id, username: user.username, identity_type: 'platform',
          grants: [{ role: 'FINANCE', scope_slug: 'shenglang' }], capabilities: ['PLATFORM_PASS_REPAYMENT_V1'] },
      }))
    }, input)
    const page = await context.newPage(), errors = [], responses = []
    let dropped = false
    page.on('pageerror', error => errors.push(error.message))
    page.on('dialog', dialog => dialog.accept())
    page.on('response', response => { if (new URL(response.url()).pathname.startsWith('/api/')) responses.push(`${new URL(response.url()).pathname}: ${response.status()}`) })
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const target = new URL(route.request().url())
      try {
        const response = await route.fetch({ url: input.api + target.pathname + target.search,
          headers: { ...route.request().headers(), host: 'admin.esports.club' } })
        if (target.pathname.endsWith('/allocate') && !dropped && response.ok()) {
          dropped = true
          await route.abort('failed')
        } else await route.fulfill({ response })
      } catch {
        responses.push(`${target.pathname}: route failed`)
        try { await route.abort('failed') } catch { /* The page may already be closed. */ }
      }
    })
    const openDetails = async () => {
      const outer = page.locator('section').filter({ has: page.getByRole('heading', { name: '通行证退款待补资金' }) })
      await outer.getByRole('button', { name: '查看明细' }).click({ timeout: 12000 })
      const section = page.locator('section').filter({ has: page.getByRole('heading', { name: '线下补款核对' }) })
      await section.getByRole('heading', { name: '补款操作' }).waitFor({ timeout: 12000 })
      return section
    }
    try {
      await page.goto('http://127.0.0.1:14103/#/')
      let section = await openDetails()
      await section.getByLabel('外部流水号').fill('ACTION-123')
      await section.getByLabel('实收人民币（元）').fill('5.001')
      await section.getByLabel('到账时间（本机时区）').fill('2026-11-07T12:00')
      await section.getByLabel('到账凭证说明').fill('隔离测试凭证')
      await section.getByRole('button', { name: '登记到账' }).click()
      await section.getByText('请输入有效人民币金额，最多两位小数。', { exact: true }).waitFor()
      await section.getByLabel('实收人民币（元）').fill('5.00')
      await section.getByRole('button', { name: '登记到账' }).click()
      await section.getByText('ACTION-123', { exact: true }).waitFor()
      await section.getByLabel('待核实流水').selectOption({ label: 'ACTION-123 · ¥5.00' })
      await section.getByRole('button', { name: '确认核实' }).click()
      await section.getByLabel('已核实流水').selectOption({ label: 'ACTION-123 · 剩余 50 浪花', timeout: 12000 })
      await section.getByLabel('退款记录').selectOption({ label: `#${input.refund_id} · 剩余 30 浪花` })
      await section.getByLabel('分配金额（浪花）').fill('20.01')
      await section.getByRole('button', { name: '确认分配' }).click()
      await section.getByText('请输入有效店币金额，最多一位小数。', { exact: true }).waitFor()
      await section.getByLabel('分配金额（浪花）').fill('20')
      await section.getByRole('button', { name: '确认分配' }).click()
      await section.getByText(/操作结果待确认/).waitFor()
      await section.getByText(/请使用原请求重试或联系平台核对/).waitFor()
      assert.equal(dropped, true)
      await page.reload()
      section = await openDetails()
      await section.getByText(/操作结果待确认/).waitFor()
      await section.getByRole('button', { name: '使用原请求重试' }).click()
      await section.getByText(/操作结果待确认/).waitFor({ state: 'hidden' })
      await section.getByText(/退款 #.* · 20 浪花 · 请求编号/).waitFor()
      const row = section.getByRole('row').filter({ hasText: '30 浪花' }).filter({ hasText: String(input.refund_id) })
      await row.getByText('10 浪花', { exact: true }).waitFor()
      await section.getByLabel('外部流水号').fill('ACTION-123')
      await section.getByLabel('实收人民币（元）').fill('5.00')
      await section.getByLabel('到账时间（本机时区）').fill('2026-11-07T12:00')
      await section.getByLabel('到账凭证说明').fill('重复流水测试')
      await section.getByRole('button', { name: '登记到账' }).click()
      await section.getByText('该到账流水已登记或正在核对', { exact: true }).waitFor()
      assert.deepEqual(errors, [])
    } catch (error) {
      console.error('page:', (await page.locator('body').innerText()).slice(0, 4000), responses, errors)
      throw error
    }
    await page.unrouteAll({ behavior: 'ignoreErrors' })
    await context.close()
  } finally { await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
