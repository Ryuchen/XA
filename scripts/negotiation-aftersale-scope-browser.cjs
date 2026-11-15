const fs = require('node:fs')
const assert = require('node:assert/strict')
const {chromium} = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless:true, args:['--no-proxy-server']})
  const context = await browser.newContext({viewport:{width:390,height:844}})
  await context.addInitScript(identity => {
    localStorage.setItem('xa:store:shenglang:xa:fronted:boss:session:v1', JSON.stringify({token:identity.token,
      user:{id:identity.id, role:'boss', nickname:'隔离测试', permissions:[]}, sessionId:'scope-browser'}))
  }, input.boss)
  const page = await context.newPage()
  let markDropped, dropped = false
  const droppedResponse = new Promise(resolve => { markDropped = resolve })
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const url = new URL(route.request().url())
    const response = await route.fetch({url:input.api + url.pathname + url.search,
      headers:{...route.request().headers(), host:'sl.esports.club'}})
    if (!dropped && route.request().method() === 'POST' && url.pathname.endsWith('/aftersale/')) {
      dropped = true; await route.abort('failed'); markDropped()
    } else await route.fulfill({response})
  })
  try {
    await page.goto(`http://127.0.0.1:14101/boss/#/order-detail?id=${input.order}`)
    await page.getByRole('button', {name:'提交或核对售后申请'}).click()
    const dialog = page.getByRole('dialog', {name:'提交订单售后'})
    await dialog.getByText('已确认终止的陪玩不能再次申请售后', {exact:false}).waitFor()
    await dialog.getByLabel('涉及的陪玩').click()
    const options = page.getByRole('option')
    assert.equal(await options.count(), 1)
    assert.match(await options.first().innerText(), new RegExp(input.remainingName))
    await options.first().click()
    await dialog.getByLabel('问题说明').fill('继续处理 B 的服务问题')
    await dialog.getByRole('button', {name:'提交问题'}).click()
    await Promise.race([droppedResponse, new Promise((_, reject) => setTimeout(() => reject(new Error('售后请求未完成')), 15000))])
    await page.reload()
    await page.getByRole('button', {name:'提交或核对售后申请'}).click()
    const recovery = page.getByRole('dialog', {name:'核对原售后申请'})
    await recovery.getByRole('button', {name:'核对原申请'}).click()
    await page.getByText('售后问题已提交', {exact:false}).first().waitFor()
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true)
  } finally {
    await page.unrouteAll({behavior:'ignoreErrors'})
    await context.close()
    await browser.close()
  }
})().catch(error => {console.error(error.stack || error); process.exitCode = 1})
