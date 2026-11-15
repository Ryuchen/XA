// Three real web clients against an isolated ASGI/SQLite backend.
const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true, args: ['--no-proxy-server'] })
  let closing = false
  try {
    async function open(role, identity, url) {
      const desktop = role === 'admin'
      const context = await browser.newContext({ viewport: desktop ? { width: 1366, height: 1024 } : { width: 390, height: 844 }, isMobile: !desktop, hasTouch: !desktop })
      await context.addInitScript(({ role, identity }) => {
        if (role === 'admin') sessionStorage.setItem('xa:fronted:admin:active-store:v1', 'shenglang')
        localStorage.setItem(`xa:store:shenglang:xa:fronted:${role}:session:v1`, JSON.stringify({ token: identity.token,
          user: { id: identity.id, role, nickname: '隔离测试', permissions: identity.permissions || [] }, sessionId: 'chat-order-card-isolated' }))
      }, { role, identity })
      const page = await context.newPage()
      await page.route(url => url.pathname.startsWith('/api/'), async route => {
        try {
          const target = new URL(route.request().url())
          const response = await route.fetch({ url: input.api + target.pathname + target.search,
            headers: { ...route.request().headers(), host: role === 'admin' ? 'admin.esports.club' : new URL(input.api).host } })
          await route.fulfill({ response })
        } catch (error) {
          if (!closing) throw error
        }
      })
      await page.goto(url)
      return page
    }
    const boss = await open('boss', input.boss, `http://127.0.0.1:14101/boss/#/chat?order_id=${input.order}`)
    await boss.getByText(`关联订单：${input.orderNo}`, { exact: true }).waitFor()
    await boss.getByLabel('消息内容').fill('老板订单咨询')
    await boss.getByRole('button', { name: '发送消息' }).click()
    const bossCard = boss.getByRole('link', { name: /查看订单/ }).filter({ hasText: input.orderNo })
    await bossCard.waitFor()
    assert.ok((await bossCard.getAttribute('href')).includes(`order-detail?id=${input.order}`))

    const provider = await open('provider', input.provider, `http://127.0.0.1:14102/player/#/support-chat?order_no=${input.orderNo}`)
    await provider.getByLabel('咨询内容').fill('陪玩订单咨询')
    await provider.getByRole('button', { name: '发送消息' }).click()
    const playerCard = provider.getByRole('link', { name: /查看订单/ }).filter({ hasText: input.orderNo })
    await playerCard.waitFor()
    assert.ok((await playerCard.getAttribute('href')).includes(`orders?order=${input.order}`))

    const admin = await open('admin', input.staff, `http://127.0.0.1:14103/#/chat?session_id=${input.bossSession}`)
    await admin.getByRole('button', { name: '领取会话' }).click()
    const adminCard = admin.getByRole('link', { name: /查看订单和售后/ }).filter({ hasText: input.orderNo })
    await adminCard.waitFor()
    assert.ok((await adminCard.getAttribute('href')).includes(`order_no_exact=${input.orderNo}`))
    await admin.goto(`http://127.0.0.1:14103/#/chat?session_id=${input.providerSession}`)
    await admin.getByRole('button', { name: '领取会话' }).click()
    await admin.getByRole('link', { name: /查看订单和售后/ }).filter({ hasText: input.orderNo }).waitFor()
    await provider.goto(`http://127.0.0.1:14102/player/#/support-chat?order_no=${input.poolOrderNo}`)
    await provider.getByLabel('咨询内容').fill('待抢订单咨询')
    await provider.getByRole('button', { name: '发送消息' }).click()
    const poolCard = provider.getByRole('link', { name: /查看订单/ }).filter({ hasText: input.poolOrderNo })
    await poolCard.waitFor()
    await poolCard.click()
    await provider.waitForURL(url => url.hash.includes(`order=${input.poolOrder}`))
    await provider.getByRole('button', { name: `查看订单 ${input.poolOrderNo} 详情` }).waitFor()
    process.stdout.write('three-client order cards passed\n')
  } finally { closing = true; await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
