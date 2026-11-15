const { spawn } = require('node:child_process')
const { chromium } = require('playwright')
const path = require('node:path')
const fs = require('node:fs')
const assert = require('node:assert/strict')

const root = path.resolve('design-demos/huashu-20261106-admin-sidebar')
const server = spawn(process.execPath, ['server.cjs', '--host', '127.0.0.1', '--port', '7100'], { cwd: root, stdio: 'pipe' })
let browser
;(async () => {
  let ready = false
  for (let attempt = 0; attempt < 50; ++attempt) {
    try { ready = (await fetch('http://127.0.0.1:7100/')).ok } catch {}
    if (ready) break
    await new Promise(resolve => setTimeout(resolve, 100))
  }
  assert.ok(ready, 'Preview server did not start')
  browser = await chromium.launch({ headless: true })
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.goto('http://127.0.0.1:7100/', { waitUntil: 'networkidle' })
    assert.equal(await page.locator('.direction').count(), 3)
    for (const variant of ['C', 'D', 'F']) {
      const response = await page.request.get(`http://127.0.0.1:7100/dir-${variant}.html`)
      assert.ok(response.ok())
      assert.match(await response.text(), /SidebarPrototype.mount/)
    }
    await page.screenshot({ path: path.join(root, 'index-gallery.png'), fullPage: true })
    assert.deepEqual(errors, [])
    console.log(JSON.stringify({ preview: 'passed', directions: 3, errors, serverStopped: false }))
  } finally {
    await browser.close()
  }
})().catch(error => { console.error(error.stack); process.exitCode = 1 }).finally(async () => {
  if (server.exitCode === null && server.signalCode === null) {
    const stopped = new Promise(resolve => server.once('exit', resolve))
    server.kill()
    await stopped
  }
  console.log(JSON.stringify({ serverStopped: true, screenshotBytes: fs.existsSync(path.join(root, 'index-gallery.png')) ? fs.statSync(path.join(root, 'index-gallery.png')).size : 0 }))
})
