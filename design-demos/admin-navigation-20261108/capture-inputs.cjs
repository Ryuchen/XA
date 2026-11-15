const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require('../../node_modules/playwright')

const root = path.resolve(__dirname)
const output = path.join(root, 'references')
const credentials = JSON.parse(fs.readFileSync('.tools/fronted-acceptance/identities.private.json', 'utf8')).roles.admin

async function currentNavigation(browser) {
  const context = await browser.newContext({ viewport: { width: 1366, height: 1024 } })
  const page = await context.newPage()
  const errors = []
  const writes = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/api/**', route => {
    const request = route.request()
    if (['GET', 'HEAD', 'OPTIONS'].includes(request.method()) || request.url().includes('/admin/auth/password-login')) return route.continue()
    writes.push({ method: request.method(), path: new URL(request.url()).pathname })
    return route.abort('blockedbyclient')
  })
  try {
    await page.goto('http://admin.esports.club/#/login')
    await page.locator('#username').fill(credentials.username)
    await page.locator('#password').fill(credentials.password)
    const response = page.waitForResponse(item => item.url().includes('/admin/auth/password-login') && item.request().method() === 'POST')
    await page.getByRole('button', { name: '获取短信验证码', exact: true }).click()
    await response
    await page.locator('#sms-code').waitFor()
    const messages = JSON.parse(fs.readFileSync('.tools/fronted-acceptance/sms-delivery.private.json', 'utf8'))
    await page.locator('#sms-code').fill(messages['13900010003'].code)
    await page.getByRole('button', { name: '验证并继续', exact: true }).click()
    await page.locator('.admin-application').waitFor({ timeout: 30000 })
    if (await page.locator('[data-admin-sidebar]').getAttribute('data-sidebar-expanded') !== 'true') await page.locator('[data-admin-sidebar-toggle]').click()
    await page.locator('[data-admin-primary-nav="orders"]').click()
    await page.locator('[data-admin-secondary-nav="orders"]').waitFor()
    await page.evaluate(() => document.fonts.ready)
    await page.screenshot({ path: path.join(output, 'current-navigation.png'), animations: 'disabled' })
    return { screenshot: 'references/current-navigation.png', errors, writes }
  } finally {
    await context.close()
  }
}

async function reference(browser, name, url) {
  const context = await browser.newContext({ viewport: { width: 1366, height: 1024 } })
  const page = await context.newPage()
  try {
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 })
    await page.waitForTimeout(1600)
    await page.screenshot({ path: path.join(output, `${name}-page.png`), animations: 'disabled' })
    const candidates = await page.locator('img').evaluateAll(images => images.map(image => {
      const box = image.getBoundingClientRect()
      return { src: image.currentSrc, alt: image.alt, naturalWidth: image.naturalWidth, naturalHeight: image.naturalHeight, x: box.x, y: box.y }
    }).filter(image => image.naturalWidth >= 480 && image.naturalHeight >= 240 && !image.src.endsWith('.svg')))
    const locator = page.locator('img').filter({ visible: true })
    let captured = false
    for (let index = 0; index < await locator.count(); index += 1) {
      const image = locator.nth(index)
      const dimensions = await image.evaluate(element => ({ width: element.naturalWidth, height: element.naturalHeight, src: element.currentSrc }))
      if (dimensions.width < 480 || dimensions.height < 240 || dimensions.src.endsWith('.svg')) continue
      await image.scrollIntoViewIfNeeded()
      await page.waitForTimeout(250)
      await image.screenshot({ path: path.join(output, `${name}-interface.png`), animations: 'disabled' })
      captured = true
      break
    }
    return { url, screenshot: captured ? `references/${name}-interface.png` : `references/${name}-page.png`, capturedInterface: captured, candidates: candidates.slice(0, 10) }
  } catch (error) {
    return { url, error: error.message }
  } finally {
    await context.close()
  }
}

;(async () => {
  const browser = await chromium.launch({ headless: true })
  const result = { current: null, references: [] }
  try {
    result.references.push(await reference(browser, 'linear', 'https://linear.app/now/behind-the-latest-design-refresh'))
    result.references.push(await reference(browser, 'shopify', 'https://help.shopify.com/en/manual/shopify-admin/shopify-admin-overview'))
  } finally {
    await browser.close()
  }
  fs.writeFileSync(path.join(output, 'capture-result.json'), JSON.stringify(result, null, 2))
  console.log(JSON.stringify(result, null, 2))
})().catch(error => { console.error(error.message); process.exitCode = 1 })
