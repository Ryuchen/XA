// Real 375px admin page using isolated HTTP; no business responses are replaced.
const fs = require('node:fs'), path = require('node:path'), net = require('node:net')
const assert = require('node:assert/strict'), { spawn } = require('node:child_process')
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
    await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.token)
    const page = await context.newPage(), origins = new Set([base, input.api])
    await page.route('**/*', route => origins.has(new URL(route.request().url()).origin) ? route.continue() : route.abort())
    await page.goto(`${base}/chat`, { waitUntil: 'domcontentloaded' })
    const first = page.locator('.session-item').filter({ hasText: input.names[0] })
    const second = page.locator('.session-item').filter({ hasText: input.names[1] })
    const text = page.locator('.input-area textarea')
    await first.click({ timeout: 30000 }); await text.fill('第一位老板的回复')
    await second.click(); await text.fill('第二位老板的草稿')
    await first.click(); assert.equal(await text.inputValue(), '第一位老板的回复')
    await second.click(); assert.equal(await text.inputValue(), '第二位老板的草稿')
    assert.match(await second.getAttribute('class'), /\bactive\b/, 'highlighted session must match the visible conversation')
    assert.doesNotMatch(await first.getAttribute('class'), /\bactive\b/)
    assert.notEqual(await second.evaluate(element => getComputedStyle(element).boxShadow), 'none', 'current session remains distinguishable from hover')
    await first.click(); assert.equal(await text.inputValue(), '第一位老板的回复')
    await page.locator('.input-footer').getByRole('button', { name: '发送' }).click()
    await page.locator('.msg-row').getByText('第一位老板的回复', { exact: true }).waitFor({ timeout: 15000 })
    assert.equal(await text.inputValue(), '')
    await second.click(); assert.equal(await text.inputValue(), '第二位老板的草稿')
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true)
    const dir = path.resolve('.tools/admin-chat-drafts-375-20261031')
    fs.mkdirSync(dir, { recursive: true })
    await page.screenshot({ path: path.join(dir, 'separate-draft.png') })
    console.log('PASS real admin drafts at 375px')
  } finally {
    if (browser) await browser.close()
    vite.kill()
  }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
