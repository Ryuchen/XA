const fs = require('node:fs')
const {chromium} = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless:true, args:['--no-proxy-server']})
  const context = await browser.newContext({viewport:{width:1366,height:1024}})
  await context.addInitScript(identity => {
    sessionStorage.setItem('xa:fronted:admin:active-store:v1', 'shenglang')
    localStorage.setItem('xa:store:shenglang:xa:fronted:admin:session:v1', JSON.stringify({token:identity.token,
      user:{id:identity.id, role:'staff', nickname:'隔离客服', permissions:identity.permissions}, sessionId:'staff-case-browser'}))
  }, input.staff)
  const page = await context.newPage()
  let queue = Promise.resolve()
  await page.route(url => url.pathname.startsWith('/api/'), route => {
    const execute = async () => {
      const url = new URL(route.request().url())
      const response = await route.fetch({url:input.api + url.pathname + url.search,
        headers:{...route.request().headers(), host:'admin.esports.club'}})
      await route.fulfill({response})
    }
    queue = queue.then(execute, execute)
    return queue
  })
  try {
    await page.goto(`http://127.0.0.1:14103/#/orders?detail=${input.order}`)
    const detail = page.getByRole('dialog', {name:/订单详情/})
    await detail.getByRole('button', {name:'登记协商售后'}).click()
    const dialog = page.getByRole('dialog', {name:'登记协商售后'})
    await dialog.getByText('此操作不代表双方同意，也不会退款', {exact:false}).waitFor()
    await dialog.getByLabel(input.provider).check()
    if (input.absentProvider && await dialog.getByLabel(input.absentProvider).count()) throw new Error('已退出陪玩仍可选择')
    await dialog.getByLabel('老板咨询说明').fill('老板提出协商终止服务')
    await dialog.getByRole('button', {name:'确认'}).click()
    await page.getByText(input.continuing ? '追加协商方案和退款暂未接通' : '售后申请已登记', {exact:false}).waitFor()
    if (input.continuing) {
      await page.reload()
      const refreshed = page.getByRole('dialog', {name:/订单详情/})
      await refreshed.getByRole('button', {name:'协商方案'}).click()
      const proposal = page.getByRole('dialog', {name:'协商方案'})
      await proposal.getByText('追加方案、当事人确认与退款尚未开放', {exact:false}).waitFor({timeout:10000})
        .catch(async error => { console.error('当前弹窗:', JSON.stringify(await page.getByRole('dialog').allTextContents()).slice(0, 2500)); throw error })
      await proposal.getByLabel(/拟退款/).fill('500')
      await proposal.getByText('剩余可退', {exact:false}).waitFor()
      await proposal.getByText('另需从钱包及押金核对', {exact:false}).waitFor()
      if (input.funding) {
        await proposal.getByRole('button', {name:'核对资金来源'}).click()
        await proposal.getByText('账面金额不足', {exact:false}).waitFor()
        await proposal.getByText('此结果仅供核对，未冻结资金', {exact:false}).waitFor()
      }
      if (!await proposal.getByRole('button', {name:'暂不可提交'}).isDisabled()) throw new Error('只读测算允许提交')
    }
  } finally {
    await page.unrouteAll({behavior:'ignoreErrors'}); await context.close(); await browser.close()
  }
})().catch(error => {console.error(error.stack || error); process.exitCode = 1})
