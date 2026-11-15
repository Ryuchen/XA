// Finance browser against an isolated ASGI store, with real synthetic ledgers.
const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({headless:true, args:['--no-proxy-server']})
  try {
    const context = await browser.newContext({viewport:{width:1366,height:1024}})
    await context.addInitScript(user => sessionStorage.setItem('xa:fronted:platform:session:v1', JSON.stringify({
      token:user.token, refresh:user.refresh, sessionId:'finance-pass-points-test',
      profile:{id:user.id,username:user.username,identity_type:'platform',grants:[{role:'FINANCE',scope_slug:'shenglang'}]},
    })), input)
    const page = await context.newPage(), errors = [], responses = []
    page.on('pageerror', error => errors.push(error.message))
    page.on('response', response => { if (new URL(response.url()).pathname.startsWith('/api/') || response.status() >= 400) responses.push(`${new URL(response.url()).pathname}: ${response.status()}`) })
    page.on('requestfailed', request => { if (request.failure()?.errorText !== 'net::ERR_ABORTED') responses.push(`${new URL(request.url()).pathname}: ${request.failure()?.errorText}`) })
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const target = new URL(route.request().url())
      const response = await route.fetch({url:input.api + target.pathname + target.search,
        headers:{...route.request().headers(), host:'admin.esports.club'}})
      await route.fulfill({response})
    })
    await page.goto('http://127.0.0.1:14103/#/')
    const section = page.locator('section').filter({has:page.getByRole('heading',{name:'通行证退款待补资金'})})
    try { await section.getByRole('button', {name:'查看明细'}).click({timeout:12000}) }
    catch (error) { console.error('isolated page:', page.url(), (await page.locator('body').innerText()).slice(0, 2000), responses, errors); throw error }
    const good = section.getByRole('row').filter({hasText:input.accounts[0]})
    await good.getByText('200 积分（抵 2 浪花）', {exact:true}).waitFor()
    await good.getByText('200 积分', {exact:true}).waitFor()
    await good.getByText('18 浪花', {exact:true}).first().waitFor()
    const bad = section.getByRole('row').filter({hasText:input.accounts[1]})
    await bad.getByText('待核对', {exact:true}).first().waitFor()
    assert.equal(await bad.getByText('待核对', {exact:true}).count(), 2)
    assert.deepEqual(errors, [])
    await page.unrouteAll({behavior:'ignoreErrors'})
    await context.close()
    process.stdout.write('platform finance points refund browser passed\n')
  } finally { await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
