/* Synthetic read-only browser check against the existing development frontend.
   Every API response is intercepted; no real customer or money data is used. */
const assert = require('node:assert/strict')
const { chromium } = require('playwright')

async function main() {
  const browser = await chromium.launch({ headless: true })
  try {
    for (const [host, slug, prefix, nickname] of [
      ['sl.esports.club', 'shenglang', 'SL', 'SL-小星'],
      ['xa.esports.club', 'xinan', 'XA', '小星'],
    ]) {
      const page = await browser.newPage({ viewport: { width: 390, height: 844 } })
      const errors = []
      page.on('pageerror', error => errors.push(error.message))
      await page.route('**/*', async route => {
        const url = new URL(route.request().url())
        if (url.hostname !== host) return route.abort()
        if (!url.pathname.startsWith('/api/')) return route.continue()
        let data = []
        if (url.pathname === '/api/stores/current/') {
          data = { id: 1, slug, name: '隔离测试门店', currency_name: '测试币', provider_name_prefix: prefix,
            status: 'ACTIVE', is_active: true, boss_url: `http://${host}/boss/`,
            provider_url: `http://${host}/player/`, admin_url: 'http://admin.esports.club/' }
        } else if (url.pathname === '/api/users/escorts/') {
          data = [{ id: 7, nickname, can_order: true, display_tags: [], qualifications: [],
            game_category_names: [], monthly_order_amount: 0, avatar: '' }]
        } else if (url.pathname === '/api/users/escorts/7/') {
          data = { id: 7, nickname, can_order: true, display_tags: [], qualifications: [],
            game_category_names: [], monthly_order_amount: 0, avatar: '' }
        } else if (url.pathname === '/api/users/qualification-options/') {
          data = {}
        }
        return route.fulfill({ status: 200, contentType: 'application/json',
          headers: { 'X-Store-Slug': slug }, body: JSON.stringify({ code: 0, data }) })
      })
      await page.goto(`http://${host}/boss/#/companions`, { waitUntil: 'domcontentloaded' })
      const name = page.locator('.boss-companion-name strong')
      await name.waitFor({ timeout: 30000 })
      assert.equal(await name.innerText(), `${prefix}-小星`)
      assert.equal(await name.locator('.boss-provider-prefix').innerText(), `${prefix}-`)
      await page.getByLabel('搜索陪玩').fill(`${prefix}-小星`)
      assert.equal(await name.count(), 1)
      await page.locator('.boss-companion-name').click()
      const title = page.locator('.boss-companion-profile-name h1')
      await title.waitFor({ timeout: 10000 })
      assert.equal(await title.innerText(), `${prefix}-小星`)
      assert.equal(await title.locator('.boss-provider-prefix').innerText(), `${prefix}-`)
      assert.deepEqual(errors, [])
      process.stdout.write(`${slug}: directory, search and detail rendered ${prefix}-小星\n`)
      await page.close()
    }
  } finally {
    await browser.close()
  }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
