// Compiled admin UI and real isolated Django APIs; only static assets are served locally.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict')
const {chromium} = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8')), assets = path.resolve(input.root)

;(async () => {
  assert.match(input.origin, /^http:\/\/(localhost|127\.0\.0\.1):\d+$/)
  const browser = await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless:true})
  try {
    const context = await browser.newContext({viewport:{width:375,height:812},isMobile:true,hasTouch:true})
    await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.staffToken)
    await context.route('**/*', route => {
      const url = new URL(route.request().url())
      if (url.origin !== input.origin) return route.abort()
      if (url.pathname.startsWith('/api/')) return route.continue()
      if (url.pathname === '/messages' || /^\/assets\/[\w.-]+$/.test(url.pathname))
        return route.fulfill({path:path.join(assets, url.pathname === '/messages' ? 'index.html' : url.pathname)})
      return route.abort()
    })
    const page = await context.newPage()
    page.setDefaultTimeout(12000)
    await page.goto(input.origin + '/messages')
    await page.getByRole('button', {name:'推送消息'}).click()
    const dialog = page.getByRole('dialog', {name:'推送站内消息'})
    await dialog.getByLabel('标题', {exact:true}).fill('隔离业务账号通知')
    await dialog.getByLabel('搜索接收人').fill('隔离同名')
    await dialog.getByRole('button', {name:'搜索', exact:true}).click()
    for (const accountNo of [input.bossNo, input.providerNo]) {
      const option = dialog.locator('.mg-option').filter({hasText:accountNo})
      await option.waitFor()
      assert(await option.getByRole('checkbox').isEnabled())
      await option.click()
    }
    await dialog.getByText(/已选 2 人/).waitFor()
    await dialog.getByLabel('摘要', {exact:true}).fill('隔离测试摘要')
    await dialog.getByRole('button', {name:'核对并推送'}).click()
    const review = page.getByRole('dialog', {name:'核对站内消息'})
    await review.getByText(new RegExp(input.bossNo)).waitFor()
    await review.getByText(new RegExp(input.providerNo)).waitFor()
    await review.getByRole('button', {name:'返回修改'}).click()
    await dialog.getByRole('button', {name:'核对并推送'}).click()
    await review.getByRole('button', {name:'确认推送'}).click()
    await page.getByText('已创建 2 条站内消息记录').waitFor()
    const api = async (url, token) => {
      const response = await page.request.get(input.origin + url, {headers:{Authorization:'Bearer ' + token}})
      const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result)); return result.data
    }
    for (const token of [input.bossToken, input.providerToken]) {
      const inbox = await api('/api/messages/', token)
      assert.equal(inbox.messages.filter(item => item.title === '隔离业务账号通知').length, 1)
    }
    assert(!(await api('/api/messages/', input.otherToken)).messages.some(item => item.title === '隔离业务账号通知'))
    const providerRoot = path.resolve(input.providerRoot)
    const provider = await browser.newContext({viewport:{width:375,height:812},isMobile:true,hasTouch:true})
    await provider.addInitScript(({token,id}) => {
      localStorage.setItem('token', JSON.stringify({data:token}))
      localStorage.setItem('xa_provider_user', JSON.stringify({data:{id:String(id),role:'provider',nickname:'隔离陪玩',username:'isolated-current-provider',avatar:''}}))
    }, {token:input.providerToken,id:input.providerId})
    await provider.route('**/*', route => {
      const url = new URL(route.request().url())
      if (url.origin !== input.origin) return route.abort()
      if (url.pathname.startsWith('/api/')) return route.continue()
      if (url.pathname === '/' || /^\/assets\/[\w.-]+$/.test(url.pathname))
        return route.fulfill({path:path.join(providerRoot, url.pathname === '/' ? 'index.html' : url.pathname)})
      return route.abort()
    })
    const providerPage = await provider.newPage()
    providerPage.setDefaultTimeout(12000)
    await providerPage.goto(input.origin + '/#/messages')
    await providerPage.getByRole('button', {name:/隔离业务账号通知/}).click()
    await providerPage.getByRole('dialog', {name:'隔离业务账号通知'}).getByText('隔离测试摘要').waitFor()
    assert.equal((await api('/api/messages/', input.providerToken)).messages.find(item => item.title === '隔离业务账号通知').is_read, true)
    console.log('Real admin and provider UIs delivered and read the two explicit business accounts only')
  } finally { await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
