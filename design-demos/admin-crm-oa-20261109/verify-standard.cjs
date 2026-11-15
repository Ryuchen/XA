const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')
const { pathToFileURL } = require('node:url')
const { chromium } = require('../../node_modules/playwright')
const root = __dirname
const data = require('./standard-data.cjs')

;(async () => {
  const browser = await chromium.launch({ headless: true })
  const result = { directions: [], thumbnails: {}, passed: false }
  try {
    for (const [index, direction] of data.directions.entries()) {
      const context = await browser.newContext({ viewport: { width: 1366, height: 1024 } })
      const page = await context.newPage()
      const errors = []
      const network = []
      const checks = []
      page.on('pageerror', error => errors.push(error.message))
      page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
      page.on('request', request => { if (/^https?:/.test(request.url())) network.push(request.url()) })
      await page.goto(pathToFileURL(path.join(root, `dir-${index + 1}.html`)).href)
      await page.locator('.record-table').waitFor()
      assert.equal(await page.locator('.domain-trigger').count(), direction.domains.length)
      assert.equal(await page.locator('.record-table th').count(), 10)
      assert.equal(await page.locator('.record-table input[type="checkbox"]').count(), 0)
      assert.ok((await page.locator('.table-footer').innerText()).includes('每页 10 条'))
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 1366)
      assert.ok(await page.locator('.table-scroll').evaluate(element => element.scrollWidth > element.clientWidth))
      assert.equal(await page.locator('.table-scroll').evaluate(element => getComputedStyle(element).overflowX), 'auto')
      const bodyText = await page.locator('body').innerText()
      for (const unwanted of ['按业务对象，建立稳定归属', '服务生命周期驱动', '岗位工作区', '沿服务链路']) assert.ok(!bodyText.includes(unwanted))
      const filterY = await page.locator('.search-order').evaluate(element => Math.round(element.getBoundingClientRect().y))
      assert.equal(await page.locator('.search-customer').evaluate(element => Math.round(element.getBoundingClientRect().y)), filterY)
      await page.screenshot({ path: path.join(root, `dir-${index + 1}.png`), fullPage: true, animations: 'disabled' })
      checks.push('标准订单页面、真实字段、每页10条、横向滚动与筛选对齐')
      assert.equal(await page.locator('.sidebar-nav [data-page="stores"]').count(), 0)
      assert.equal(await page.locator('#platform-navigation [data-page="stores"]').count(), 1)
      assert.equal(await page.locator('#quick-dispatch').count(), direction.key === 'A' ? 1 : 0)
      if (direction.key === 'A') {
        await page.locator('#quick-dispatch').click()
        assert.equal(await page.locator('#quick-dialog').evaluate(dialog => dialog.open), true)
        assert.ok(page.url().endsWith('#/orders?dispatch=quick'))
        await page.screenshot({ path: path.join(root, 'dir-1-quick.png'), animations: 'disabled' })
        await page.locator('#quick-close').click()
        assert.ok(page.url().endsWith('#/orders'))
      }
      checks.push('平台管理独立，A方案快捷派单可打开与关闭')

      await page.locator('.search-order').fill('测试订单')
      assert.ok((await page.locator('#filter-note').innerText()).includes('请点击查询'))
      await page.locator('#order-filter button[type="submit"]').click()
      assert.ok((await page.locator('#filter-note').innerText()).includes('筛选条件已更新'))
      await page.locator('#advanced-toggle').click()
      assert.equal(await page.locator('#advanced-filters').isVisible(), true)
      assert.equal(await page.locator('#advanced-toggle').getAttribute('aria-expanded'), 'true')
      await page.locator('[data-queue="客服待派"]').click()
      assert.equal(await page.locator('[data-queue="客服待派"]').getAttribute('aria-pressed'), 'true')
      await page.locator('#clear-filters').click()
      assert.equal(await page.locator('.search-order').inputValue(), '')
      assert.equal(await page.locator('[data-queue="全部订单"]').getAttribute('aria-pressed'), 'true')
      await page.locator('#refresh-preview').click()
      assert.equal(await page.locator('#prototype-toast').isVisible(), true)
      checks.push('查询、更多筛选、队列、清除与刷新均为真实前端状态')

      for (const domain of direction.domains) {
        const button = page.locator(`.domain-trigger[data-domain="${domain.id}"]`).first()
        if (await button.getAttribute('aria-expanded') === 'true') await button.click()
        await button.click()
        assert.equal(await page.locator('.domain-trigger[aria-expanded="true"]').count(), 1)
        assert.equal(await page.locator('.secondary .page-link').count(), domain.centers.reduce((total, center) => total + center.features.length, 0))
      }
      checks.push('全部一级模块、二级分组与三级页面数量正确')

      await page.keyboard.press('Control+k')
      assert.equal(await page.locator('.search-result').count(), 45)
      await page.locator('#search-input').fill('统一字典')
      assert.equal(await page.locator('.search-result').count(), 1)
      await page.locator('.search-result').click()
      assert.ok(page.url().endsWith('#/dictionaries'))
      assert.ok((await page.locator('#breadcrumbs').innerText()).includes('系统管理'))
      assert.ok((await page.locator('#breadcrumbs').innerText()).includes('门店设置'))
      assert.ok((await page.locator('#breadcrumbs').innerText()).includes('统一字典'))
      checks.push('完整45页搜索、统一字典归属和面包屑正确')

      const last = direction.domains[direction.domains.length - 1]
      const lastButton = page.locator(`.domain-trigger[data-domain="${last.id}"]`).first()
      if (await lastButton.getAttribute('aria-expanded') !== 'true') await lastButton.click()
      await lastButton.click()
      assert.equal(await page.locator('.domain-trigger[aria-expanded="true"]').count(), 0)
      await page.locator('#collapse').click()
      assert.equal(Math.round(await page.locator('.sidebar').evaluate(element => element.getBoundingClientRect().width)), 72)
      await page.locator(`[data-domain="${direction.defaultDomain}"]`).click()
      assert.ok(page.url().endsWith('#/orders'))
      await page.locator('#collapse').click()
      await page.locator('#store-trigger').click()
      await page.locator('[data-store="xinan"]').click()
      assert.equal(await page.locator('#store-trigger-label').innerText(), '兴安电竞')
      await page.locator('#store-trigger').click()
      await page.locator('[data-store="shenglang"]').click()
      checks.push('侧栏收起、收起后导航与门店上下文切换正常')

      await page.locator('.record-table').waitFor()
      await page.setViewportSize({ width: 1920, height: 1080 })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 1920)
      await page.screenshot({ path: path.join(root, `dir-${index + 1}-wide.png`), fullPage: true, animations: 'disabled' })
      await page.setViewportSize({ width: 375, height: 812 })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 375)
      await page.locator('#mobile-toggle').click()
      await page.waitForFunction(() => Math.abs(document.querySelector('.sidebar').getBoundingClientRect().x) < 1)
      await page.locator('.secondary .page-link').first().click()
      assert.equal(await page.locator('#app').evaluate(element => element.classList.contains('mobile-open')), false)
      await page.screenshot({ path: path.join(root, `dir-${index + 1}-mobile.png`), fullPage: true, animations: 'disabled' })
      checks.push('1920桌面、375窄屏及移动导航可用')
      assert.deepEqual(errors, [])
      assert.deepEqual(network, [])
      result.directions.push({ key: direction.key, checks, errors, network, passed: true })
      await context.close()
    }
    const context = await browser.newContext({ viewport: { width: 1600, height: 1100 } })
    const page = await context.newPage()
    await page.goto(pathToFileURL(path.join(root, 'thumbs.html')).href)
    assert.equal(await page.locator('.thumb-card').count(), 6)
    assert.equal(await page.locator('.thumb-card').first().evaluate(element => Math.round(element.getBoundingClientRect().width)), 480)
    assert.equal(await page.locator('.thumb-stage').first().evaluate(element => Math.round(element.getBoundingClientRect().height)), 300)
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    await page.screenshot({ path: path.join(root, 'thumbs.png'), fullPage: true, animations: 'disabled' })
    result.thumbnails = { cards: 6, passed: true }
    await context.close()
    result.passed = true
  } catch (error) {
    result.error = error.stack
  } finally {
    await browser.close()
  }
  fs.writeFileSync(path.join(root, 'verification.json'), JSON.stringify(result, null, 2))
  console.log(JSON.stringify(result, null, 2))
  if (!result.passed) process.exitCode = 1
})()
