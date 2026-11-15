// Actual provider page and isolated ASGI; no external payment or message is sent.
const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless:true})
  try {
    const context = await browser.newContext({viewport:{width:390,height:844}, isMobile:true, hasTouch:true})
    await context.addInitScript(({token,id}) => localStorage.setItem('xa:store:shenglang:xa:fronted:provider:session:v1',
      JSON.stringify({token, user:{id,role:'provider',nickname:'隔离提现测试'}, sessionId:'withdraw-rules-test'})),
      {token:input.token,id:input.account})
    const page = await context.newPage(), errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/api/**', async route => {
      const target = new URL(route.request().url())
      const response = await route.fetch({url:input.api + target.pathname + target.search,
        headers:{...route.request().headers(), host:new URL(input.api).host}})
      await route.fulfill({response})
    })
    await page.goto('http://127.0.0.1:14102/player/#/wallet')
    await page.getByRole('heading', {name:'我的钱包'}).waitFor()
    await page.getByRole('button', {name:'提现',exact:true}).click()
    let dialog = page.getByRole('dialog')
    await dialog.locator('input').first().fill('100')
    await dialog.locator('select').selectOption(String(input.payee))
    await dialog.getByText('预计实际打款为零，请调整金额或核对费率').waitFor()
    assert.equal(await dialog.getByRole('button', {name:'确认提现'}).isDisabled(), true)
    await dialog.getByRole('button', {name:'取消'}).click()
    const response = await fetch(input.api + '/api/admin/config/withdraw', {
      method:'PUT', headers:{Authorization:'Bearer ' + input.staffToken,'Content-Type':'application/json'},
      body:JSON.stringify({min_amount:0,balance_threshold:0,tax_rate:2})
    })
    assert.equal((await response.json()).code, 0)
    await page.getByRole('button', {name:'提现',exact:true}).click()
    dialog = page.getByRole('dialog')
    await dialog.locator('input').first().fill('100')
    await dialog.locator('select').selectOption(String(input.payee))
    await page.waitForFunction(() => [...document.querySelectorAll('button')].some(
      button => button.textContent?.trim() === '确认提现' && !button.disabled), null, {timeout:10000})
    await dialog.getByRole('button', {name:'确认提现'}).click()
    await page.getByText('提现申请已提交', {exact:true}).waitFor({timeout:10000})
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true)
    assert.deepEqual(errors, [])
    process.stdout.write('provider withdrawal rule browser passed\n')
  } finally { await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
