// Real HTTP messages, with the saved message forwarded as a socket transport event.
const fs = require('node:fs')
const path = require('node:path')
const net = require('node:net')
const assert = require('node:assert/strict')
const { spawn } = require('node:child_process')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
async function freePort() {
  const server = net.createServer()
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const port = server.address().port
  await new Promise(resolve => server.close(resolve))
  return port
}
;(async () => {
  const port = await freePort(), root = path.resolve('apps/admin-web')
  const vite = spawn(process.execPath, [path.join(root, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(port), '--strictPort'],
    { cwd: root, env: { ...process.env, XA_ADMIN_DEV_API_TARGET: input.api }, stdio: 'ignore', windowsHide: true })
  let browser
  try {
    const base = `http://127.0.0.1:${port}`
    for (let i = 0; i < 120; i++) {
      if (vite.exitCode !== null) throw new Error('Vite exited')
      try { if ((await fetch(base)).ok) break } catch {}
      await new Promise(resolve => setTimeout(resolve, 250))
      if (i === 119) throw new Error('Vite did not start')
    }
    browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true })
    const context = await browser.newContext({ viewport: { width: 375, height: 812 } })
    await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.staffToken)
    const page = await context.newPage()
    const readBatches = []
    page.on('request', request => {
      if (request.method() !== 'POST' || !request.url().includes(`/chat-sessions/${input.sessionId}/read/`)) return
      try { const body = request.postDataJSON(); if (Array.isArray(body.message_ids)) readBatches.push(body.message_ids.length) } catch {}
    })
    const origins = new Set([base, input.api])
    await page.route('**/*', route => origins.has(new URL(route.request().url()).origin) ? route.continue() : route.abort())
    await page.goto(`${base}/chat`, { waitUntil: 'domcontentloaded' })
    await page.locator('.session-item').filter({ hasText: input.bossName }).click({ timeout: 30000 })
    await page.getByText('隔离历史消息 225', { exact: true }).waitFor({ timeout: 20000 })
    const wrap = page.locator('.msg-area .el-scrollbar__wrap')
    const geometry = await wrap.evaluate(element => ({ height: element.clientHeight, total: element.scrollHeight }))
    assert.ok(geometry.total > geometry.height + 100, `history must scroll: ${JSON.stringify(geometry)}`)
    await wrap.evaluate(element => { element.scrollTop = 0; element.dispatchEvent(new Event('scroll')) })
    await page.waitForTimeout(100)
    let oldest = 225
    while (oldest > 0) {
      const firstLoaded = page.getByText(`隔离历史消息 ${oldest}`, { exact: true })
      const beforeTop = await firstLoaded.evaluate(element => element.getBoundingClientRect().top)
      await page.getByRole('button', { name: '加载更早消息' }).click()
      oldest = Math.max(0, oldest - 50)
      await page.getByText(`隔离历史消息 ${oldest}`, { exact: true }).waitFor({ timeout: 10000 })
      const afterTop = await firstLoaded.evaluate(element => element.getBoundingClientRect().top)
      assert.ok(Math.abs(afterTop - beforeTop) < 3, `loading history should keep the reading position: ${beforeTop} -> ${afterTop}`)
      await wrap.evaluate(element => { element.scrollTop = 0; element.dispatchEvent(new Event('scroll')) })
    }
    assert.equal(await page.getByRole('button', { name: '加载更早消息' }).count(), 0)
    // Return to the oldest content before receiving another message.
    await wrap.evaluate(element => { element.scrollTop = 0; element.dispatchEvent(new Event('scroll')) })
    const response = await fetch(input.api + '/api/chat/send/', { method: 'POST',
      headers: { Authorization: 'Bearer ' + input.bossToken, 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: '隔离新消息' }) })
    const body = await response.json()
    assert.equal(body.code, 0)
    // The message itself came from the real API. Only socket delivery is replayed.
    await page.evaluate(async ({ sessionId, message }) => {
      const { adminWs } = await import('/src/utils/ws.ts')
      adminWs.dispatch('chat_message', { session_id: sessionId, message })
    }, { sessionId: input.sessionId, message: body.data })
    await page.locator('.msg-area .bubble').filter({ hasText: '隔离新消息' }).waitFor({ timeout: 10000 })
    await page.waitForTimeout(200)
    assert.ok(await wrap.evaluate(element => element.scrollTop) < 40, 'new message must not jump away from history')
    // A disconnected socket may miss more than one page of messages. The
    // real API writes them; reconnect must fetch back to the loaded window.
    for (let index = 0; index < 60; index++) {
      const sent = await fetch(input.api + '/api/chat/send/', { method: 'POST',
        headers: { Authorization: 'Bearer ' + input.bossToken, 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: `隔离离线消息 ${index}` }) })
      assert.equal((await sent.json()).code, 0)
    }
    await page.evaluate(async () => {
      const { adminWs } = await import('/src/utils/ws.ts')
      adminWs.dispatch('connected', {})
    })
    await page.locator('.msg-area').getByText('隔离离线消息 0', { exact: true }).waitFor({ timeout: 10000 })
    await page.locator('.msg-area').getByText('隔离离线消息 59', { exact: true }).waitFor({ timeout: 10000 })
    assert.ok(await wrap.evaluate(element => element.scrollTop) < 40, 'reconnect must preserve the history position')
    const sessions = await fetch(input.api + '/api/admin/chat-sessions/', { headers: { Authorization: 'Bearer ' + input.staffToken } }).then(r => r.json())
    assert.equal(sessions.data.list.find(row => row.id === input.sessionId).unread_support, 286, 'unseen new messages and older history remain unread')
    const output = path.resolve('.tools/admin-chat-history-scroll-20261031')
    fs.mkdirSync(output, { recursive: true })
    await page.screenshot({ path: path.join(output, 'history.png') })
    await page.getByRole('button', { name: '回到最新消息' }).click()
    await page.waitForFunction(() => { const element = document.querySelector('.msg-area .el-scrollbar__wrap'); return element && element.scrollHeight - element.clientHeight - element.scrollTop < 4 })
    await page.waitForTimeout(200)
    const after = await fetch(input.api + '/api/admin/chat-sessions/', { headers: { Authorization: 'Bearer ' + input.staffToken } }).then(r => r.json())
    assert.equal(after.data.list.find(row => row.id === input.sessionId).unread_support, 0, 'viewing latest acknowledges message')
    assert.deepEqual(readBatches.slice(-2), [200, 86], `loaded messages must be acknowledged in bounded batches: ${readBatches}`)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true)
    await page.screenshot({ path: path.join(output, 'latest.png') })
    console.log('PASS real chat history with incoming message at 375px')
  } finally {
    if (browser) await browser.close()
    vite.kill()
  }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
