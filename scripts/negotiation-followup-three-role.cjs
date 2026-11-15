const fs = require('node:fs')
const assert = require('node:assert/strict')
const {chromium} = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless:true, args:['--no-proxy-server']})
  const contexts = [], pages = []
  let simulateDisabledFollowup = false
  let markDropped
  const executionDropped = new Promise(resolve => { markDropped = resolve })
  async function open(role, identity, host, viewport) {
    const context = await browser.newContext({viewport}); contexts.push(context)
    await context.addInitScript(({role, identity}) => {
      if (role === 'admin') sessionStorage.setItem('xa:fronted:admin:active-store:v1', 'shenglang')
      localStorage.setItem(`xa:store:shenglang:xa:fronted:${role}:session:v1`, JSON.stringify({token:identity.token,
        user:{id:identity.id, role:role === 'admin' ? 'staff' : role, nickname:'隔离测试', permissions:identity.permissions || []},
        sessionId:'followup-browser'}))
    }, {role, identity})
    const page = await context.newPage(); pages.push(page)
    let queue = Promise.resolve(), dropped = false
    await page.route(url => url.pathname.startsWith('/api/'), route => {
      const run = async () => {
        const url = new URL(route.request().url())
        const response = await route.fetch({url:input.api + url.pathname + url.search,
          headers:{...route.request().headers(), host}})
        if (role === 'admin' && simulateDisabledFollowup && route.request().method() === 'GET' &&
            url.pathname.endsWith('/negotiation-execution/') && !url.search) {
          const body = await response.json()
          body.data.execution_enabled = false
          await route.fulfill({response, json:body})
        } else if (role === 'admin' && !dropped && route.request().method() === 'POST' && url.pathname.endsWith('/negotiation-execution/')) {
          dropped = true; await route.abort('failed'); markDropped()
        } else await route.fulfill({response})
      }
      queue = queue.then(run, run); return queue
    })
    return page
  }
  const admin = await open('admin', input.staff, 'admin.esports.club', {width:1366,height:1024})
  const boss = await open('boss', input.boss, 'sl.esports.club', {width:390,height:844})
  const player = await open('provider', input.provider, 'sl.esports.club', {width:390,height:844})
  try {
    await admin.goto(`http://127.0.0.1:14103/#/orders?detail=${input.order}`)
    await admin.getByRole('dialog', {name:/订单详情/}).getByRole('button', {name:'协商方案'}).click()
    const editor = admin.getByRole('dialog', {name:'协商方案'})
    await editor.getByLabel(/拟退款/).fill('100')
    await editor.getByText(/拟保留收益 300/).waitFor()
    await editor.getByLabel('协商说明').fill('继续履约陪玩的协商退款')
    await editor.getByRole('button', {name:'确认'}).click()
    await admin.getByText('方案已登记，等待当事人确认').waitFor()
    await boss.goto(`http://127.0.0.1:14101/boss/#/order-detail?id=${input.order}`)
    const bossPanel = boss.getByRole('region', {name:'协商方案'})
    await bossPanel.getByText(/拟退还：100/).waitFor()
    await bossPanel.getByRole('button', {name:'同意方案'}).click()
    await bossPanel.getByRole('button', {name:'确认提交'}).click()
    await player.goto(`http://127.0.0.1:14102/player/#/orders?order=${input.order}`)
    await player.getByRole('button', {name:`查看订单 ${input.orderNo} 详情`}).click()
    const playerPanel = player.getByRole('region', {name:'协商方案'})
    await playerPanel.getByText(/本人拟保留收益：300/).waitFor()
    await playerPanel.getByRole('button', {name:'同意方案'}).click()
    await playerPanel.getByRole('button', {name:'确认提交'}).click()
    await playerPanel.getByText('所需人员均已确认，等待客服处理。', {exact:false}).waitFor()
    // Exercise the UI response to an unavailable follow-up gate; the server
    // enforces the switch independently in the API integration test.
    simulateDisabledFollowup = true
    await admin.reload()
    await admin.getByRole('dialog', {name:/订单详情/}).getByRole('button', {name:'执行协商退款'}).click()
    let execution = admin.getByRole('dialog', {name:'确认执行协商退款'})
    await execution.getByText('追加协商处理暂未开放', {exact:false}).waitFor()
    await execution.getByLabel('已核对当事人、退款金额及保留收益，确认按该方案处理。').check()
    assert.equal(await execution.getByRole('button', {name:'确认退款'}).isDisabled(), true)
    await execution.getByRole('button', {name:'返回'}).click()
    simulateDisabledFollowup = false
    await admin.reload()
    await admin.getByRole('dialog', {name:/订单详情/}).getByRole('button', {name:'执行协商退款'}).click()
    execution = admin.getByRole('dialog', {name:'确认执行协商退款'})
    await execution.getByText('账面金额可覆盖', {exact:false}).waitFor()
    await execution.getByLabel('已核对当事人、退款金额及保留收益，确认按该方案处理。').check()
    await execution.getByRole('button', {name:'确认退款'}).click()
    await Promise.race([executionDropped, new Promise((_, reject) => setTimeout(() => reject(new Error('退款请求未完成')), 15000))])
    await admin.reload()
    await admin.getByRole('dialog', {name:/订单详情/}).getByRole('button', {name:'核对协商处理'}).click()
    execution = admin.getByRole('dialog', {name:'核对协商处理'})
    await execution.getByLabel('已核对当事人、退款金额及保留收益，确认按该方案处理。').check()
    await execution.getByRole('button', {name:'查询并重试原请求'}).click()
    await admin.getByText('协商退款已完成').waitFor()
    assert.equal(await player.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true)
    assert.equal(await boss.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true)
  } finally {
    for (const page of pages) await page.unrouteAll({behavior:'ignoreErrors'})
    for (const context of contexts) { await context.unrouteAll({behavior:'ignoreErrors'}); await context.close() }
    await browser.close()
  }
})().catch(error => {console.error(error.stack || error); process.exitCode = 1})
