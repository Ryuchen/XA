const assert = require('node:assert/strict')

// Only the browser sees these store responses. No store state is changed.
module.exports = async function checkStoreClosed(browser, origin = 'http://sl.esports.club') {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } })
  try {
    const page = await context.newPage()
    const errors = [], businessCalls = []
    page.on('pageerror', error => errors.push(error.message))
    let response = { status: 403, json: { code: 403, msg: '门店尚未开放或已停用，请联系门店管理员' } }
    let hold, reads = 0
    await page.route('**/api/**', async route => {
      const request = route.request()
      if (request.method() !== 'GET' || new URL(request.url()).pathname !== '/api/stores/current/') {
        businessCalls.push(request.method() + ' ' + new URL(request.url()).pathname)
        return route.abort()
      }
      reads++
      if (hold) await hold.promise
      return route.fulfill(response)
    })
    const url = origin + '/boss/#/login'
    hold = Promise.withResolvers()
    await page.goto(url)
    await page.getByRole('heading', { name: '正在确认门店…', exact: true }).waitFor()
    await page.getByText('声浪电竞', { exact: true }).waitFor()
    const art = page.locator('.xa-store-closed-art')
    assert.equal(await art.count(), 0)
    hold.resolve()
    const closed = page.getByRole('heading', { name: '门店暂未开放', exact: true })
    await closed.waitFor()
    const services = page.getByRole('heading', { name: '开放后可使用', exact: true })
    await services.waitFor()
    const help = page.getByRole('heading', { name: '已有订单或售后问题？', exact: true })
    await help.waitFor()
    await page.getByText('查看自己的订单进度和售后记录。', { exact: true }).waitFor()
    await page.getByText('本店开通自助下单后，可选服务并核对价格。', { exact: true }).waitFor()
    await page.getByText('暂时无法登录、下单或查看订单。', { exact: true }).waitFor()
    const schedule = page.getByText('开放时间以门店通知为准', { exact: true })
    await schedule.waitFor()
    assert.ok((await help.boundingBox()).y < (await services.boundingBox()).y, 'Phone users see help before the service preview')
    const address = page.getByRole('textbox', { name: '门店入口链接' })
    assert.equal(await address.inputValue(), origin + '/boss/')
    await page.evaluate(() => {
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: { writeText: () => Promise.reject(new DOMException('Denied', 'NotAllowedError')) },
      })
    })
    const copy = page.getByRole('button', { name: '复制入口链接' })
    await copy.click()
    await page.getByRole('status').filter({ hasText: '入口链接已选中，请手动复制' }).waitFor()
    assert.equal(await address.evaluate(element => element === document.activeElement &&
      element.selectionStart === 0 && element.selectionEnd === element.value.length), true)
    await page.evaluate(() => {
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: { writeText: async value => { window.copiedEntryLink = value } },
      })
    })
    await copy.focus()
    await page.keyboard.press('Enter')
    await page.getByRole('status').filter({ hasText: '入口链接已复制' }).waitFor()
    assert.equal(await page.evaluate(() => window.copiedEntryLink), origin + '/boss/')
    assert.equal(page.url(), url)
    const retry = page.getByRole('button', { name: '重新检查状态', exact: true })
    assert.ok((await schedule.boundingBox()).y < (await retry.boundingBox()).y, 'The opening notice sits above the status action')
    assert.ok((await art.boundingBox()).y < (await help.boundingBox()).y, 'The artwork shares the hero and does not add another mobile panel')
    await retry.click({ trial: true })
    const before = reads
    hold = Promise.withResolvers()
    await retry.focus()
    await page.keyboard.press('Enter')
    const pending = page.getByRole('button', { name: '正在检查…', exact: true })
    await pending.waitFor()
    assert.equal(await pending.getAttribute('aria-disabled'), 'true')
    await page.keyboard.press('Enter')
    response = { status: 503, json: { code: 503, msg: '门店服务暂不可用，请稍后重试' } }
    hold.resolve()
    await page.getByRole('alert').filter({ hasText: response.json.msg }).waitFor()
    assert.equal(reads, before + 1)
    assert.equal(await closed.count(), 0)
    assert.equal(await schedule.count(), 0)
    assert.equal(await art.count(), 0)
    assert.equal(await services.count(), 0)
    assert.equal(await address.count(), 0)
    assert.equal(await page.getByRole('button', { name: '重试', exact: true }).evaluate(element => element === document.activeElement), true)

    response = { status: 403, json: { code: 403, msg: '门店数据库尚未配置，请联系平台管理员' } }
    await page.getByRole('button', { name: '重试', exact: true }).click()
    await page.getByRole('alert').filter({ hasText: response.json.msg }).waitFor()
    assert.equal(await closed.count(), 0)
    assert.deepEqual(businessCalls, [])
    response = { status: 200, json: { code: 0, data: {
      id: 1, slug: 'shenglang', name: '契约验证门店', currency_name: '浪花',
      status: 'ACTIVE', is_active: true, boss_url: '/boss/', provider_url: '/player/', admin_url: '/',
    } } }
    await page.getByRole('button', { name: '重试', exact: true }).click()
    await page.getByRole('heading', { name: '老板账号登录', exact: true }).waitFor()
    assert.equal(page.url(), url)
    assert.deepEqual(errors, [])
  } finally {
    await context.close()
  }
}
