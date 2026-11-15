const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')
const { pathToFileURL } = require('node:url')
const { chromium } = require('../../node_modules/playwright')
const root = __dirname
const data = JSON.parse(fs.readFileSync(path.join(root, 'navigation-options.json'), 'utf8'))

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
      await page.locator('.domain-trigger').first().waitFor()
      assert.equal(await page.locator('.domain-trigger').count(), direction.domains.length)
      assert.equal(await page.locator('.global-link').count(), 2)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 1366)
      const unnamedButtons = await page.locator('button').evaluateAll(buttons => buttons.filter(button => !button.textContent.trim() && !button.getAttribute('aria-label') && !button.title).length)
      assert.equal(unnamedButtons, 0)
      await page.screenshot({ path: path.join(root, `dir-${index + 1}.png`), fullPage: true, animations: 'disabled' })
      checks.push('1366×1024、无横向溢出、按钮有可访问名称')

      await page.locator('#directory-open').click()
      assert.equal(await page.locator('#directory-dialog').evaluate(dialog => dialog.open), true)
      const directoryPaths = await page.locator('.directory-page').evaluateAll(buttons => buttons.map(button => button.dataset.page))
      assert.equal(directoryPaths.length, 45)
      assert.equal(new Set(directoryPaths).size, 45)
      assert.ok(directoryPaths.includes('dictionaries'))
      await page.screenshot({ path: path.join(root, `dir-${index + 1}-directory.png`), fullPage: false, animations: 'disabled' })
      await page.locator('#directory-close').click()
      checks.push('完整目录45页且仅出现一次，统一字典可达')

      for (const domain of direction.domains) {
        const domainButton = page.locator(`.domain-trigger[data-domain="${domain.id}"]`).first()
        if (await domainButton.getAttribute('aria-expanded') === 'true') {
          await domainButton.click()
          assert.equal(await page.locator('.domain-trigger[aria-expanded="true"]').count(), 0)
        }
        await domainButton.click()
        assert.equal(await page.locator('.domain-trigger[aria-expanded="true"]').count(), 1)
        assert.equal(await page.locator('.secondary .page-link').count(), domain.centers.reduce((total, center) => total + center.features.length, 0))
        assert.equal(await page.locator('.center-row').count(), domain.centers.length)
        assert.equal(await page.locator('.secondary').getAttribute('id'), `menu-${domain.id}`)
      }
      checks.push('所有一级域可展开、二级中心和三级页面数量正确')

      await page.keyboard.press('Control+k')
      await page.locator('#search-input').fill('统一字典')
      assert.equal(await page.locator('.search-result').count(), 1)
      await page.locator('.search-result').click()
      assert.ok(page.url().endsWith('#/dictionaries'))
      assert.ok((await page.locator('#breadcrumbs').innerText()).includes('统一字典'))
      await page.keyboard.press('Control+k')
      await page.locator('#search-input').fill('提现')
      assert.equal(await page.locator('.search-result').count(), 1)
      await page.keyboard.press('Escape')
      checks.push('搜索、快捷键与三级页面跳转正确')

      const settingsId = direction.domains[direction.domains.length - 1].id
      const settingsButton = page.locator(`.domain-trigger[data-domain="${settingsId}"]`).first()
      if (await settingsButton.getAttribute('aria-expanded') !== 'true') await settingsButton.click()
      await settingsButton.click()
      assert.equal(await page.locator('.domain-trigger[aria-expanded="true"]').count(), 0)
      await page.locator('#collapse').click()
      assert.equal(Math.round(await page.locator('.sidebar').evaluate(element => element.getBoundingClientRect().width)), 72)
      await page.locator(`[data-domain="${settingsId}"]`).click()
      assert.ok(page.url().includes(direction.domains[direction.domains.length - 1].centers[0].features[0][0]))
      await page.locator('#collapse').click()
      checks.push('域折叠、侧栏收起及收起后导航正确')

      await page.locator('#store-trigger').click()
      await page.locator('[data-store="xinan"]').click()
      assert.equal(await page.locator('#store-trigger-label').innerText(), '兴安电竞')
      assert.ok((await page.title()).includes('兴安电竞'))
      await page.locator('#store-trigger').click()
      await page.locator('[data-store="shenglang"]').click()
      assert.equal(await page.locator('#store-trigger-label').innerText(), '声浪电竞')
      checks.push('门店上下文切换演示正常，无业务网络请求')

      await page.locator(`[data-domain="${direction.defaultDomain}"]`).first().click()
      await page.setViewportSize({ width: 1920, height: 1080 })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 1920)
      await page.screenshot({ path: path.join(root, `dir-${index + 1}-wide.png`), fullPage: true, animations: 'disabled' })
      await page.setViewportSize({ width: 375, height: 812 })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 375)
      await page.locator('#mobile-toggle').click()
      await page.waitForFunction(() => Math.abs(document.querySelector('.sidebar').getBoundingClientRect().x) < 1)
      assert.ok(Math.abs(await page.locator('.sidebar').evaluate(element => element.getBoundingClientRect().x)) < 1)
      await page.locator('.secondary .page-link').first().click()
      assert.equal(await page.locator('#app').evaluate(element => element.classList.contains('mobile-open')), false)
      await page.screenshot({ path: path.join(root, `dir-${index + 1}-mobile.png`), fullPage: true, animations: 'disabled' })
      checks.push('1920桌面与375窄屏无横向溢出，窄屏导航可达')
      assert.deepEqual(errors, [])
      assert.deepEqual(network, [])
      result.directions.push({ key: direction.key, checks, errors, network, passed: true })
      await context.close()
    }
    const context = await browser.newContext({ viewport: { width: 1600, height: 1100 } })
    const page = await context.newPage()
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.goto(pathToFileURL(path.join(root, 'thumbs.html')).href)
    assert.equal(await page.locator('.card').count(), 6)
    assert.equal(await page.locator('.card').first().evaluate(element => Math.round(element.getBoundingClientRect().width)), 480)
    assert.equal(await page.locator('.stage').first().evaluate(element => Math.round(element.getBoundingClientRect().height)), 300)
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    await page.screenshot({ path: path.join(root, 'thumbs.png'), fullPage: true, animations: 'disabled' })
    assert.deepEqual(errors, [])
    result.thumbnails = { cards: 6, errors, passed: true }
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
