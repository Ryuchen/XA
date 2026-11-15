const { chromium } = require('playwright')
const { pathToFileURL } = require('node:url')
const path = require('node:path')
const fs = require('node:fs')
const assert = require('node:assert/strict')

const root = path.resolve('design-demos/huashu-20261106-admin-sidebar')
const mode = process.argv[2] || 'thumbs'
;(async () => {
  const browser = await chromium.launch({ headless: true })
  const results = []
  try {
    if (mode === 'thumbs') {
      const page = await browser.newPage({ viewport: { width: 1536, height: 800 } })
      const errors = []
      page.on('pageerror', error => errors.push(error.message))
      await page.goto(pathToFileURL(path.join(root, 'thumbs.html')).href)
      await page.waitForFunction(() => document.querySelectorAll('.prototype-app').length === 6)
      await page.screenshot({ path: path.join(root, 'thumbs.png'), fullPage: true })
      assert.deepEqual(errors, [])
      results.push({ file: 'thumbs.png', apps: 6, errors })
      await page.close()
    } else {
      for (const variant of ['C', 'D', 'F']) {
        const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
        const errors = []
        const checks = []
        page.on('pageerror', error => errors.push(error.message))
        await page.goto(pathToFileURL(path.join(root, `dir-${variant}.html`)).href)
        await page.locator('.prototype-app').waitFor()
        assert.equal(await page.locator('.prototype-content h1').innerText().then(text => text.split('交互设计原型')[0].trim()), '订单与派单')
        checks.push('真实菜单与当前项加载')
        await page.screenshot({ path: path.join(root, `dir-${variant}.png`), fullPage: true })
        await page.locator('[data-domain="providers"]').first().click()
        assert.equal(await page.locator('.prototype-subnav-header strong, .domain-trigger[data-domain="providers"]').first().innerText(), '陪玩中心')
        await page.getByRole('button', { name: '陪玩业绩', exact: true }).click()
        assert.match(await page.locator('.prototype-content h1').innerText(), /陪玩业绩/)
        checks.push('业务分组与真实子菜单切换')
        await page.locator('[data-store-menu]').click()
        await page.locator('[data-store="xinan"]').click()
        assert.match(await page.locator('.store-current').innerText(), /兴安电竞/)
        assert.match(await page.locator('.prototype-toast').innerText(), /仅原型预览/)
        checks.push('门店切换仅影响原型')
        await page.locator('[data-search]').click()
        await page.getByRole('searchbox', { name: '搜索菜单', exact: true }).fill('报单审核')
        assert.equal(await page.locator('.search-result:visible').count(), 1)
        await page.getByRole('button', { name: '报单审核 订单中心', exact: true }).click()
        assert.match(await page.locator('.prototype-content h1').innerText(), /报单审核/)
        checks.push('菜单搜索与结果定位')
        await page.locator('[data-collapse]').first().click()
        assert.ok(await page.locator('.prototype-app.is-collapsed').count())
        await page.locator('[data-collapse]').first().click()
        assert.equal(await page.locator('.prototype-app.is-collapsed').count(), 0)
        checks.push('侧栏收起与展开')
        await page.locator('[data-pin]').first().click().catch(() => {})
        await page.setViewportSize({ width: 1366, height: 1024 })
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= 1366))
        await page.setViewportSize({ width: 375, height: 812 })
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= 375))
        checks.push('1366 与窄屏原型无页面横向溢出')
        assert.deepEqual(errors, [])
        results.push({ file: `dir-${variant}.html`, screenshot: `dir-${variant}.png`, checks, errors })
        await page.close()
      }
    }
  } catch (error) {
    results.push({ passed: false, error: error.stack })
  } finally {
    await browser.close()
  }
  fs.writeFileSync(path.join(root, `capture-${mode}-results.json`), JSON.stringify(results, null, 2))
  console.log(JSON.stringify(results, null, 2))
  if (results.some(result => result.passed === false)) process.exitCode = 1
})().catch(error => { console.error(error); process.exitCode = 1 })
