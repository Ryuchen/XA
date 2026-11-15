const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless:true, args:['--no-proxy-server']})
  const context = await browser.newContext({viewport:{width:1366,height:1024}})
  const errors = []
  await context.addInitScript(identity => {
    sessionStorage.setItem('xa:fronted:admin:active-store:v1', 'shenglang')
    localStorage.setItem('xa:store:shenglang:xa:fronted:admin:session:v1', JSON.stringify({token:identity.token,
      user:{id:identity.id, role:'staff', permissions:identity.permissions}, sessionId:'admin-prestart-handoff'}))
  }, input.staff)
  const page = await context.newPage()
  page.on('pageerror', error => errors.push(error.message))
  // Reach the configured local gateway through loopback while retaining the
  // admin virtual host; all business API traffic is routed to isolated ASGI.
  await page.route(url => url.hostname === '127.0.0.1' && !url.pathname.startsWith('/api/'), async route => {
    const response = await route.fetch({headers:{...route.request().headers(), host:'admin.esports.club'}})
    await route.fulfill({response})
  })
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const target = new URL(route.request().url())
    const response = await route.fetch({url:input.api + target.pathname + target.search,
      headers:{...route.request().headers(), host:'admin.esports.club'}})
    await route.fulfill({response})
  })
  try {
    await page.goto(`http://127.0.0.1/#/orders?detail=${input.orderId}`)
    const detail = page.getByRole('dialog').filter({hasText:'订单详情'})
    await detail.getByRole('button', {name:'转单', exact:true}).click()
    const dialog = page.getByRole('dialog', {name:input.originalTransfer ? '转单与收益分配' : '开始服务前更换陪玩'})
    if (input.legacy) {
      await dialog.locator('#legacy-minimum-choice').selectOption('NONE')
      await dialog.locator('#legacy-original-requirement').fill('原规则不限等级')
      await dialog.locator('#legacy-minimum-evidence').fill('原价目与历史记录核对')
    }
    await dialog.getByRole('combobox', {name:'转入陪玩'}).click()
    await page.getByRole('option').filter({hasText:'隔离接替陪玩'}).click()
    if (input.originalTransfer) {
      if (process.env.XA_SEGMENT_FINANCE_BROWSER === '1') {
        await dialog.getByText('收益将根据成交份额和对应抽成核对，目前不能结算。').waitFor()
        assert.equal(await dialog.getByText('原陪玩保留方式').count(), 0)
      }
      await dialog.locator('#original-retained-0').fill(input.fractional ? '300' : '400')
      await dialog.locator('#original-transferred-0').fill('600')
      await dialog.locator('#original-agreement-0').fill('双方确认原价份额')
    }
    await dialog.locator('#transfer-reason').fill('客服核对后更换陪玩')
    await dialog.getByRole('checkbox', {name:input.originalTransfer ? (process.env.XA_SEGMENT_FINANCE_BROWSER === '1' ? '已核对转出与转入人员、原价份额及处罚金额' : '已核对转出与转入人员、原陪玩保留收益及处罚金额') : '已核对原陪玩、接替陪玩和订单安排'}).check()
    await dialog.getByRole('button', {name:'确认转单'}).click()
    await dialog.waitFor({state:'hidden'})
    await page.getByText('转单已完成').waitFor()
    await page.getByRole('button', {name:'详情', exact:true}).first().click()
    const auditTitle = input.originalTransfer ? '转单原价份额记录' : '服务前更换陪玩记录'
    const audit = page.locator('section').filter({has:page.getByRole('heading', {name:auditTitle})})
    await audit.getByRole('heading', {name:auditTitle}).waitFor()
    if (input.originalTransfer) await audit.getByText(/双方确认原价份额/).waitFor()
    else {
      await audit.getByText(/隔离原陪玩 → 隔离接替陪玩/).waitFor()
      await audit.getByText(/客服核对后更换陪玩/).waitFor()
    }
    if (process.env.XA_SEGMENT_FINANCE_BROWSER === '1') {
      const finance = page.locator('section').filter({has:page.getByRole('heading', {name:'接入时成交与抽成快照'})})
      await finance.getByText('本记录用于核对，不代表实际结算或打款。').waitFor()
      await finance.getByText(input.gameTransfer || input.standardTransfer ? /已记录成交基数及接入抽成，逐段结算待核对/ : /已记录成交基数，接入抽成及结算待核对/).waitFor()
      await finance.getByText(/接入时预计收益/).first().waitFor()
      await finance.getByText(input.fractional ? /原陪玩保留 33\.6，接替陪玩 67\.4/ : /原陪玩保留 400，接替陪玩 600/).waitFor()
      if (input.fractional) await finance.getByText(/接入时预计收益 80\.8/).waitFor()
      if (input.gameTransfer) await finance.getByText(/抽成 50% · 接入时预计收益 300/).waitFor()
      if (input.standardTransfer) await finance.getByText(/抽成 35% · 接入时预计收益 390/).waitFor()
    }
    if (input.legacy) await audit.getByText(/原价目与历史记录核对/).waitFor()
    assert.deepEqual(errors, [])
  } finally { await page.unrouteAll({behavior:'ignoreErrors'}); await context.close(); await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
