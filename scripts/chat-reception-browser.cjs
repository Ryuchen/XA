// Real admin UI against an isolated test ASGI backend.
const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true, args: ['--no-proxy-server'] })
  try {
    async function open(identity) {
      const context = await browser.newContext({ viewport: { width: 1366, height: 1024 } })
      await context.addInitScript(who => {
        sessionStorage.setItem('xa:fronted:admin:active-store:v1', 'shenglang')
        localStorage.setItem('xa:store:shenglang:xa:fronted:admin:session:v1', JSON.stringify({ token: who.token,
          user: { id: who.id, role: 'staff', permissions: who.permissions }, sessionId: 'chat-reception-isolated' }))
      }, identity)
      const page = await context.newPage()
      await page.route(url => url.pathname.startsWith('/api/'), async route => {
        const target = new URL(route.request().url())
        const response = await route.fetch({ url: input.api + target.pathname + target.search,
          headers: { ...route.request().headers(), host: 'admin.esports.club' } })
        await route.fulfill({ response })
      })
      await page.goto(`http://127.0.0.1:14103/#/chat?session_id=${input.session}`)
      return page
    }
    const a = await open(input.a)
    const aPanel = a.locator('[data-chat-session] > section').nth(1)
    await a.getByText('请先领取会话，再查看和回复。').waitFor()
    assert.equal(await aPanel.getByText('隔离咨询内容').count(), 0)
    await a.getByRole('button', { name: '领取会话' }).click()
    await aPanel.getByText('隔离咨询内容').waitFor()
    const b = await open(input.b)
    const bPanel = b.locator('[data-chat-session] > section').nth(1)
    await b.getByText('该会话由其他客服接待，聊天内容仅接待人和主管可查看。').waitFor()
    assert.equal(await bPanel.getByText('隔离咨询内容').count(), 0)
    await a.getByRole('button', { name: '转交／分配' }).click()
    await a.getByLabel('选择接待客服').selectOption(String(input.b.id))
    await a.getByRole('button', { name: '确认分配' }).click()
    await a.getByText('该会话由其他客服接待，聊天内容仅接待人和主管可查看。').waitFor()
    assert.equal(await aPanel.getByText('隔离咨询内容').count(), 0)
    await b.getByRole('button', { name: '重新读取' }).click()
    await bPanel.getByText('隔离咨询内容').waitFor()
    process.stdout.write('isolated chat reception browser passed\n')
  } finally { await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
