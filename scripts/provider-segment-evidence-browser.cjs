const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless:true, args:['--no-proxy-server']})
  const context = await browser.newContext({viewport:{width:390,height:844}, isMobile:true, hasTouch:true})
  await context.addInitScript(identity => localStorage.setItem('xa:store:shenglang:xa:fronted:provider:session:v1',
    JSON.stringify({token:identity.token, user:{id:identity.id, role:'provider', nickname:'隔离陪玩'}, sessionId:'segment-evidence'})), input.provider)
  const page = await context.newPage(), errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route(url => url.hostname === '127.0.0.1' && !url.pathname.startsWith('/api/'), async route => {
    const response = await route.fetch({headers:{...route.request().headers(), host:'sl.esports.club'}})
    await route.fulfill({response})
  })
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const target = new URL(route.request().url())
    const response = await route.fetch({url:input.api + target.pathname + target.search,
      headers:{...route.request().headers(), host:'sl.esports.club'}})
    await route.fulfill({response})
  })
  try {
    await page.goto(`http://127.0.0.1/player/#/orders?order=${input.order}`)
    await page.getByRole('button', {name:/查看订单 .* 详情/}).click()
    await page.getByRole('heading', {name:'我的履约记录'}).waitFor()
    await page.getByText('第 1 次履约', {exact:true}).waitFor()
    await page.getByText('第 2 次履约', {exact:true}).waitFor()
    for (const label of ['第 1 次履约入队截图', '第 2 次履约入队截图', '第 2 次履约结单截图']) {
      const picture = page.getByRole('img', {name:label})
      await picture.waitFor()
      await picture.evaluate(img => img.decode())
    }
    assert.equal(await page.getByRole('img', {name:'第 1 次履约结单截图'}).count(), 0)
    const first = page.locator('article').filter({hasText:'第 1 次履约'})
    await first.getByLabel('选择本次履约结单截图').setInputFiles({name:'completion.png', mimeType:'image/png',
      buffer:Buffer.from(input.image, 'base64')})
    await first.getByRole('button', {name:'保存本次结单截图'}).click()
    await first.getByText('本次履约截图已保存。').waitFor()
    await first.getByRole('img', {name:'第 1 次履约结单截图'}).evaluate(img => img.decode())
    if (input.segmentReports) {
      for (const index of [1, 2]) {
        const article = page.locator('article').filter({hasText:`第 ${index} 次履约`})
        await article.getByRole('button', {name:'创建本次履约报单'}).click()
        await article.getByLabel('选择本次战绩截图').setInputFiles({name:'result.png', mimeType:'image/png', buffer:Buffer.from(input.image, 'base64')})
        await article.getByRole('button', {name:'上传本次战绩截图'}).click()
        await article.getByRole('img', {name:'本次战绩截图'}).waitFor()
        await article.getByRole('button', {name:'提交本次履约报单'}).click()
        await article.getByText('本次履约报单 · 待审核').waitFor()
      }
    }
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true)
    assert.deepEqual(errors, [])
  } finally { await page.unrouteAll({behavior:'ignoreErrors'}); await context.close(); await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
