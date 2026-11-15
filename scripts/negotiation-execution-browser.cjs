const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({ executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    headless:true, args:['--no-proxy-server'] })
  const context = await browser.newContext({ viewport:{ width:1366, height:1024 } })
  await context.addInitScript(({ staff, api }) => {
    sessionStorage.setItem('xa:fronted:admin:active-store:v1', 'shenglang')
    localStorage.setItem('xa:store:shenglang:xa:fronted:admin:session:v1', JSON.stringify({token:staff.token,
      user:{id:staff.id, role:'staff', nickname:'隔离客服', permissions:staff.permissions}, sessionId:'execution-browser'}))
    const NativeSocket = window.WebSocket
    window.WebSocket = class extends NativeSocket {
      constructor(url, protocols) {
        const target = new URL(String(url))
        if (target.pathname.startsWith('/ws/')) { target.host = new URL(api).host; target.protocol = 'ws:' }
        super(target.toString(), ...(protocols === undefined ? [] : [protocols]))
      }
    }
  }, input)
  const page = await context.newPage()
  let dropped = false, posts = 0, signalDropped
  const events = []
  const droppedResponse = new Promise(resolve => { signalDropped = resolve })
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const target = new URL(route.request().url())
    // Simulate turning off the entry after the committed write. The actual
    // result lookup and the refund remain real requests to the isolated API.
    if (dropped && route.request().method() === 'GET' &&
        target.pathname.endsWith('/negotiation-execution/') && !target.search) {
      await route.fulfill({status:404, contentType:'application/json', body:JSON.stringify({code:404, msg:'协商退款暂未开放'})})
      return
    }
    const response = await route.fetch({ url:input.api + target.pathname + target.search,
      headers:{...route.request().headers(), host:'admin.esports.club'} })
    if (target.pathname.endsWith('/negotiation-execution/')) events.push({ method:route.request().method(), status:response.status() })
    if (route.request().method() === 'POST' && target.pathname.endsWith('/negotiation-execution/')) {
      posts++
      if (!dropped) { dropped = true; await route.abort('failed'); signalDropped(); return }
    }
    await route.fulfill({ response })
  })
  try {
    await page.goto(`http://127.0.0.1:14103/#/orders?detail=${input.order}`)
    const detail = page.getByRole('dialog', { name:/订单详情/ })
    await detail.getByRole('button', { name:input.zero ? '执行协商终止' : '执行协商退款' }).click()
    const dialog = page.getByRole('dialog', { name:input.zero ? '确认协商终止' : '确认执行协商退款' })
    await dialog.getByText(input.zero ? /退款 0/ : /退款 500/).waitFor()
    await dialog.getByText(/账面金额可覆盖/).waitFor()
    await dialog.getByRole('checkbox').check()
    await dialog.getByRole('button', { name:input.zero ? '确认终止' : '确认退款' }).click()
    await Promise.race([droppedResponse, new Promise((_, reject) => {
      const timer = setTimeout(() => reject(new Error('退款请求未完成')), 30000)
      timer.unref()
    })])
    assert.equal(events.find(event => event.method === 'POST')?.status, 200, '首笔退款必须由服务端成功提交后才丢弃响应')
    await page.getByText(/上次结果未确认/).waitFor()
    assert.equal(posts, 1)
    await page.reload()
    const restored = page.getByRole('dialog', { name:/订单详情/ })
    await restored.getByRole('button', { name:'核对协商处理' }).click()
    const recovery = page.getByRole('dialog', { name:'核对协商处理' })
    await recovery.getByRole('checkbox').check()
    await recovery.getByRole('button', { name:'查询并重试原请求' }).click()
    await page.getByText(input.zero ? '协商终止已完成' : '协商退款已完成', { exact:true }).waitFor({ timeout:10000 })
    assert.equal(posts, 1)
    assert.equal(await page.evaluate(() => Object.keys(localStorage).filter(key => key.startsWith('xa:negotiation-refund:')).length), 0)
  } catch (error) {
    console.error('Execution requests:', JSON.stringify(events))
    console.error('Visible page:', (await page.locator('body').innerText()).slice(0, 3000))
    throw error
  } finally { await page.unrouteAll({behavior:'ignoreErrors'}); await context.close(); await browser.close() }
})().catch(error => { console.error(error); process.exit(1) })
