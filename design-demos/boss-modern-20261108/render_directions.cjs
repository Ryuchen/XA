const { chromium } = require('../../node_modules/playwright')
const { pathToFileURL } = require('node:url')
const path = require('node:path')
const fs = require('node:fs')
const assert = require('node:assert/strict')

;(async () => {
  const browser = await chromium.launch({ headless: true })
  const results = []
  for (let index = 1; index <= 3; index += 1) {
    const name = `dir-${index}`
    const url = pathToFileURL(path.resolve(`design-demos/boss-modern-20261108/${name}.html`)).href
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
    const page = await context.newPage()
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.goto(url)
    await page.screenshot({ path: `design-demos/boss-modern-20261108/${name}-desktop.png`, animations: 'disabled' })
    await page.screenshot({ path: `design-demos/boss-modern-20261108/${name}-full.png`, fullPage: true, animations: 'disabled' })
    const firstGame = page.locator('[data-game]').first()
    await firstGame.click()
    assert.equal(await firstGame.getAttribute('aria-pressed'), 'true', `${name} 玩法选择失败`)
    await page.locator('.nav a[href="#companions"]').click()
    assert.equal(new URL(page.url()).hash, '#companions', `${name} 桌面导航失败`)
    const smallTargets = await page.locator('a,button').evaluateAll(elements => elements.flatMap(element => {
      const rect = element.getBoundingClientRect()
      const style = getComputedStyle(element)
      if (style.display === 'none' || style.visibility === 'hidden' || rect.width === 0) return []
      return rect.height < 44 ? [{ text: element.textContent.trim().slice(0, 30), height: rect.height, className: element.className }] : []
    }))
    assert.deepEqual(errors, [], `${name} 控制台异常`)
    await context.close()

    const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true })
    const mobilePage = await mobile.newPage()
    mobilePage.on('pageerror', error => errors.push(error.message))
    await mobilePage.goto(url)
    await mobilePage.screenshot({ path: `design-demos/boss-modern-20261108/${name}-mobile-fold.png`, animations: 'disabled' })
    await mobilePage.screenshot({ path: `design-demos/boss-modern-20261108/${name}-mobile.png`, fullPage: true, animations: 'disabled' })
    await mobilePage.locator('.dock a[href="#mine"]').click()
    assert.equal(new URL(mobilePage.url()).hash, '#mine', `${name} 手机 Dock 失败`)
    assert.equal(await mobilePage.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, `${name} 手机横向溢出`)
    assert.deepEqual(errors, [], `${name} 手机控制台异常`)
    await mobile.close()
    results.push({ name, errors, smallTargets })
    console.log(name, 'ok', 'smallTargets', smallTargets.length)
  }
  await browser.close()
  fs.writeFileSync('design-demos/boss-modern-20261108/render-results.json', JSON.stringify(results, null, 2))
})().catch(error => { console.error(error.stack); process.exitCode = 1 })
