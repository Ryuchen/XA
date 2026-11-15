const path = require('node:path')
const fs = require('node:fs')
const { chromium } = require('../../node_modules/playwright')
const root = path.join(__dirname, 'references')

async function captureImages(page, prefix, limit = 2) {
  const images = page.locator('img')
  const captured = []
  const candidates = await images.evaluateAll(elements => elements.map((image, index) => ({ index, src: image.currentSrc, alt: image.alt, width: image.naturalWidth, height: image.naturalHeight })).filter(image => image.width >= 500 && image.height >= 240))
  for (const candidate of candidates.slice(0, limit)) {
    const image = images.nth(candidate.index)
    await image.scrollIntoViewIfNeeded()
    await page.waitForFunction(element => element.complete && element.naturalWidth > 0, await image.elementHandle())
    const name = `${prefix}-${captured.length + 1}.png`
    await image.screenshot({ path: path.join(root, name), animations: 'disabled' })
    captured.push({ file: `references/${name}`, alt: candidate.alt, source: candidate.src })
  }
  return { captured, candidates: candidates.slice(0, 8) }
}

;(async () => {
  const browser = await chromium.launch({ headless: true })
  const result = []
  for (const item of [
    { name: 'ruoyi', url: 'https://github.com/yangzongzhuan/RuoYi-Vue' },
    { name: 'shadcn', url: 'https://shadcnuikit.com/admin-dashboard' },
  ]) {
    const context = await browser.newContext({ viewport: { width: 1366, height: 1024 } })
    const page = await context.newPage()
    try {
      await page.goto(item.url, { waitUntil: 'domcontentloaded', timeout: 45000 })
      await page.waitForLoadState('networkidle', { timeout: 12000 }).catch(() => {})
      const images = await captureImages(page, item.name)
      await page.screenshot({ path: path.join(root, `${item.name}-page.png`), animations: 'disabled' })
      result.push({ url: item.url, ...images })
    } catch (error) {
      result.push({ url: item.url, error: error.message })
    } finally {
      await context.close()
    }
  }
  await browser.close()
  fs.writeFileSync(path.join(root, 'capture-result.json'), JSON.stringify(result, null, 2))
  console.log(JSON.stringify(result, null, 2))
})().catch(error => { console.error(error.message); process.exitCode = 1 })
