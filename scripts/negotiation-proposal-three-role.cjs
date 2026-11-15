const fs = require('node:fs')
const assert = require('node:assert/strict')
const {chromium} = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless:true, args:['--no-proxy-server']})
  const contexts = []
  const pages = []
  async function open(role, identity, host, viewport) {
    const context = await browser.newContext({viewport})
    contexts.push(context)
    await context.addInitScript(({role, identity, api}) => {
      if (role === 'admin') sessionStorage.setItem('xa:fronted:admin:active-store:v1', 'shenglang')
      localStorage.setItem(`xa:store:shenglang:xa:fronted:${role}:session:v1`, JSON.stringify({token:identity.token,
        user:{id:identity.id, role:role === 'admin' ? 'staff' : role, nickname:'隔离测试', permissions:identity.permissions || []}, sessionId:'negotiation-browser'}))
      const NativeSocket = window.WebSocket
      window.__negotiationSockets = []
      window.WebSocket = class extends NativeSocket {
        constructor(url, protocols) {
          const target = new URL(String(url))
          if (target.pathname === '/ws/orders/') {
            target.host = new URL(api).host
            target.protocol = 'ws:'
          }
          super(target.toString(), ...(protocols === undefined ? [] : [protocols]))
          if (target.pathname === '/ws/orders/') window.__negotiationSockets.push(this)
        }
      }
    }, {role, identity, api:input.api})
    const page = await context.newPage()
    pages.push(page)
    let queue = Promise.resolve(), dropped = false
    await page.route(url => url.pathname.startsWith('/api/'), route => {
      const run = async () => {
        const url = new URL(route.request().url())
        const response = await route.fetch({url:input.api + url.pathname + url.search, headers:{...route.request().headers(), host}})
        if (!dropped && route.request().method() === 'POST' && url.pathname.endsWith('/negotiation-proposal/') && ['admin','boss'].includes(role)) {
          dropped = true
          await route.abort('failed')
        } else await route.fulfill({response})
      }
      queue = queue.then(run, run)
      return queue
    })
    return {context, page}
  }
  const admin = await open('admin', input.staff, 'admin.esports.club', {width:1366,height:1024})
  const boss = await open('boss', input.boss, 'sl.esports.club', {width:390,height:844})
  const provider = await open('provider', input.provider, 'sl.esports.club', {width:390,height:844})
  try {
    await boss.page.goto(`http://127.0.0.1:14101/boss/#/order-detail?id=${input.order}`)
    await provider.page.goto(`http://127.0.0.1:14102/player/#/orders?order=${input.order}`)
    await provider.page.getByRole('button', {name:`查看订单 ${input.orderNo} 详情`}).click()
    const bossPanel = boss.page.getByRole('region', {name:'协商方案'})
    const playerPanel = provider.page.getByRole('region', {name:'协商方案'})
    await boss.page.waitForFunction(() => window.__negotiationSockets.some(socket => socket.readyState === WebSocket.OPEN))
    await provider.page.waitForFunction(() => window.__negotiationSockets.some(socket => socket.readyState === WebSocket.OPEN))
    await admin.page.goto(`http://127.0.0.1:14103/#/orders?detail=${input.order}`)
    const detail = admin.page.getByRole('dialog', {name:/订单详情/})
    await detail.getByRole('button', {name:'协商方案'}).click()
    const editor = admin.page.getByRole('dialog', {name:'协商方案'})
    await editor.getByLabel(/拟退款/).fill('50')
    await editor.getByText(/拟保留收益 750/).waitFor()
    await editor.getByRole('button', {name:'核对资金来源'}).click()
    await editor.getByText(/待结算收益 50、钱包 0、押金 0、缺口 0/).waitFor()
    await editor.getByText(/此结果仅供核对/).waitFor()
    await editor.getByLabel(/拟退款/).fill('60')
    await editor.getByText(/拟保留收益 740/).waitFor()
    assert.equal(await editor.getByText(/此结果仅供核对/).count(), 0)
    await editor.getByLabel(/拟退款/).fill('50')
    await editor.getByText(/拟保留收益 750/).waitFor()
    await editor.getByLabel('协商说明').fill('双方协商后中止服务')
    await editor.getByRole('button', {name:'确认'}).click()
    await editor.getByRole('button', {name:'重试原方案'}).waitFor()
    await bossPanel.getByText(/拟退还：50/).waitFor()
    await playerPanel.getByText(/本人拟保留收益：750/).waitFor()
    await admin.page.reload()
    await admin.page.getByRole('dialog', {name:/订单详情/}).getByRole('button', {name:'协商方案'}).click()
    await admin.page.getByRole('dialog', {name:'协商方案'}).getByRole('button', {name:'重试原方案'}).click()
    await admin.page.getByText('方案已登记，等待当事人确认').waitFor()

    // Complete the real order while its in-service case remains open; all
    // three pages must still expose the same outstanding proposal afterward.
    await provider.page.goto(`http://127.0.0.1:14102/player/#/orders?order=${input.order}`)
    const completion = await provider.page.evaluate(async ({id, token}) => {
      const bytes = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAIAAAAmkwkpAAAAE0lEQVR4nGP8//8/AwwwwVl4OQCWbgMF7ZjH1AAAAABJRU5ErkJggg=='), c => c.charCodeAt(0))
      const form = new FormData()
      form.append('completion_image', new File([bytes], 'completion.png', {type:'image/png'}))
      const response = await fetch(`/api/orders/orders/${id}/complete/`, {method:'POST', headers:{Authorization:`Bearer ${token}`}, body:form})
      return {status:response.status, body:await response.json()}
    }, {id:input.order, token:input.provider.token})
    assert.equal(completion.status, 200, JSON.stringify(completion.body))
    assert.equal(completion.body.code, 0, JSON.stringify(completion.body))
    await admin.page.reload()
    await admin.page.getByRole('dialog', {name:/订单详情/}).getByRole('button', {name:'协商方案'}).waitFor()

    await boss.page.goto(`http://127.0.0.1:14101/boss/#/order-detail?id=${input.order}`)
    await bossPanel.getByText(/拟退还：50/).waitFor()
    await bossPanel.getByRole('button', {name:'同意方案'}).click()
    await bossPanel.getByRole('button', {name:'确认提交'}).click()
    // The commit event can resolve the deliberately lost HTTP response by
    // re-reading the saved decision while the page remains open.
    await bossPanel.getByText('已记录你的同意，等待其他人员确认。', {exact:false}).waitFor()
    await boss.page.reload()
    await bossPanel.getByText('已记录你的同意，等待其他人员确认。', {exact:false}).waitFor()
    assert.equal(await bossPanel.getByRole('button', {name:'重试原决定'}).count(), 0)

    await provider.page.goto(`http://127.0.0.1:14102/player/#/orders?order=${input.order}`)
    await provider.page.getByRole('button', {name:`查看订单 ${input.orderNo} 详情`}).click()
    await playerPanel.getByText(/本人拟保留收益：750/).waitFor()
    await playerPanel.getByRole('button', {name:'同意方案'}).click()
    await playerPanel.getByRole('button', {name:'确认提交'}).click()
    await playerPanel.getByText('所需人员均已确认，等待客服处理。', {exact:false}).waitFor()
    await bossPanel.getByText('所需人员均已确认，等待客服处理。', {exact:false}).waitFor()
    await admin.page.reload()
    await admin.page.getByRole('dialog', {name:/订单详情/}).getByRole('button', {name:'处理完成'}).click()
    const resolve = admin.page.getByRole('dialog', {name:'处理售后结案'})
    await resolve.getByLabel(/处理说明/).fill('尝试结案')
    await resolve.getByRole('button', {name:'确认处理售后结案'}).click()
    await resolve.getByText('已有各方同意的协商方案，请先完成约定处理，不能直接结束售后', {exact:false}).waitFor()
    await boss.page.goto('http://127.0.0.1:14101/boss/#/messages')
    await boss.page.getByRole('link', {name:/订单协商方案已确认/}).click()
    await boss.page.getByRole('link', {name:'查看关联订单'}).click()
    await bossPanel.getByText('所需人员均已确认，等待客服处理。', {exact:false}).waitFor()
    await provider.page.goto('http://127.0.0.1:14102/player/#/messages')
    await provider.page.getByRole('button', {name:/订单协商方案已确认/}).click()
    const notice = provider.page.getByRole('dialog', {name:'订单协商方案已确认'})
    await notice.getByRole('button', {name:'查看相关内容'}).click()
    await provider.page.waitForURL(url => url.hash.includes(`/orders?order=${input.order}`))
    await provider.page.getByRole('button', {name:`查看订单 ${input.orderNo} 详情`}).click()
    await admin.page.reload()
    await admin.page.getByRole('dialog', {name:/订单详情/}).getByRole('button', {name:'协商方案'}).click()
    const revision = admin.page.getByRole('dialog', {name:'协商方案'})
    await revision.getByLabel(/拟退款/).fill('60')
    await revision.getByText(/拟保留收益 740/).waitFor()
    await revision.getByRole('button', {name:'确认'}).click()
    await bossPanel.getByText(/拟退还：60/).waitFor()
    await playerPanel.getByText(/本人拟保留收益：740/).waitFor()
    await bossPanel.getByRole('button', {name:'同意方案'}).click()
    await bossPanel.getByText('确认同意以上版本的方案？').waitFor()
    await playerPanel.getByRole('button', {name:'提出异议'}).click()
    await playerPanel.getByRole('button', {name:'确认提交'}).click()
    await bossPanel.getByText('方案有异议，请联系客服。', {exact:false}).waitFor()
    assert.equal(await bossPanel.getByText('确认同意以上版本的方案？').count(), 0)
    assert.equal(await boss.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true)
    assert.equal(await provider.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true)
  } finally {
    for (const page of pages) await page.unrouteAll({behavior:'ignoreErrors'})
    for (const context of contexts) { await context.unrouteAll({behavior:'ignoreErrors'}); await context.close() }
    await browser.close()
  }
})().catch(error => {console.error(error.stack || error); process.exitCode = 1})
