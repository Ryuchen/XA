const assert = require('node:assert/strict')

async function checkEntryWithoutScripts(browser, origin, viewport) {
  const context = await browser.newContext({ viewport, javaScriptEnabled: false, colorScheme: 'dark' })
  try {
    for (const [path, title, help] of [
      ['/boss/', '老板服务中心', '已有订单或售后问题？'],
      ['/player/', '陪玩工作台', '有服务正在进行？'],
    ]) {
      const page = await context.newPage()
      const url = origin + path
      await page.goto(url)
      await page.getByRole('heading', { name: title, exact: true }).waitFor()
      await page.getByRole('heading', { name: help, exact: true }).waitFor()
      await page.getByText('门店状态将在页面载入后显示。', { exact: false }).waitFor()
      assert.equal(await page.getByText('门店暂未开放', { exact: true }).count(), 0)
      const fallback = page.locator('.xa-store-entry-fallback')
      assert.equal(await fallback.evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(11, 18, 32)')
      assert.equal(await fallback.evaluate(el => el.scrollWidth), viewport.width)
      const reopen = page.getByRole('link', { name: '重新打开门店入口' })
      assert.equal(await reopen.getAttribute('href'), path)
      await page.keyboard.press('Tab')
      assert.equal(await reopen.evaluate(el => el === document.activeElement), true)
      await Promise.all([page.waitForRequest(url), page.keyboard.press('Enter')])
      await page.close()
    }
  } finally {
    await context.close()
  }
}

// Only the browser sees these store responses. No store state is changed.
module.exports = async function checkStoreClosed(browser, origin = 'http://sl.esports.club') {
  const viewport = { width: 320, height: 568 }
  await checkEntryWithoutScripts(browser, origin, viewport)
  const context = await browser.newContext({ viewport })
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
      const pendingResponse = hold
      if (pendingResponse) await pendingResponse.promise
      try {
        await route.fulfill(response)
      } finally {
        pendingResponse?.handled?.resolve()
      }
    })
    const url = origin + '/boss/#/login'
    hold = Promise.withResolvers()
    await page.goto(url)
    await page.getByRole('heading', { name: '正在确认门店…', exact: true }).waitFor()
    await page.getByText('声浪电竞', { exact: true }).waitFor()
    const art = page.locator('.xa-store-closed-art')
    const closed = page.getByRole('heading', { name: '门店暂未开放', exact: true })
    const services = page.getByRole('heading', { name: '开放后可使用', exact: true })
    const returnGuide = page.getByRole('heading', { name: '收到开放通知后', exact: true })
    const help = page.getByRole('heading', { name: '已有订单或售后问题？', exact: true })
    const schedule = page.getByText('开放时间以门店通知为准', { exact: true })
    const helpChannel = page.getByText('请通过原下单渠道联系本店客服。', { exact: true })
    const copy = page.getByRole('button', { name: '复制入口链接' })
    const pending = page.getByRole('button', { name: '正在检查…', exact: true })
    await art.waitFor()
    await help.waitFor()
    await copy.waitFor()
    assert.equal(await pending.getAttribute('aria-disabled'), 'true')
    assert.equal(await closed.count(), 0)
    assert.equal(await services.count(), 0)
    assert.equal(await returnGuide.count(), 0)
    assert.equal(await schedule.count(), 0)
    await page.evaluate(() => {
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: { writeText: async value => { window.copiedEntryLink = value } },
      })
    })
    await copy.click()
    await page.getByRole('status').filter({ hasText: '入口链接已复制' }).waitFor()
    assert.equal(await page.evaluate(() => window.copiedEntryLink), origin + '/boss/')
    hold.resolve()
    await closed.waitFor()
    await services.waitFor()
    await returnGuide.waitFor()
    await page.getByText('开放后可使用本店老板账号登录。', { exact: false }).waitFor()
    await page.getByText('查看自己的订单进度和售后记录。', { exact: true }).waitFor()
    await page.getByText('本店开通自助下单后，可选服务并核对价格。', { exact: true }).waitFor()
    await page.getByText('暂时无法登录、下单或查看订单。', { exact: true }).waitFor()
    await schedule.waitFor()
    await helpChannel.waitFor()
    await page.evaluate(() => window.scrollTo(0, 0))
    const channelBox = await helpChannel.boundingBox()
    assert.ok(channelBox.y >= 0 && channelBox.y + channelBox.height <= viewport.height,
      'The support channel is readable above the fold at 320×568')
    await page.getByText('联系前请准备', { exact: true }).waitFor()
    await page.getByText('订单编号和需要处理的问题。', { exact: true }).waitFor()
    const helpBox = await help.boundingBox()
    const returnBox = await returnGuide.boundingBox()
    const servicesBox = await services.boundingBox()
    assert.ok(helpBox.y < returnBox.y && returnBox.y < servicesBox.y,
      'Phone users see support and return instructions before the service preview')
    const copyBox = await copy.boundingBox()
    assert.ok(copyBox.y + copyBox.height <= helpBox.y, 'The return action is in the hero, before the help panel')
    const address = page.getByRole('textbox', { name: '门店入口链接' })
    assert.equal(await address.count(), 0)
    const readsBeforeCopy = reads
    await page.evaluate(() => {
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: { writeText: () => Promise.reject(new DOMException('Denied', 'NotAllowedError')) },
      })
    })
    await copy.click()
    await page.getByRole('status').filter({ hasText: '入口链接已选中，请手动复制' }).waitFor()
    assert.equal(await address.inputValue(), origin + '/boss/')
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
    assert.equal(await address.count(), 0)
    assert.equal(await page.evaluate(() => window.copiedEntryLink), origin + '/boss/')
    assert.equal(await copy.evaluate(element => element === document.activeElement), true)
    assert.equal(reads, readsBeforeCopy)
    assert.equal(page.url(), url)
    const retry = page.getByRole('button', { name: '重新检查状态', exact: true })
    assert.ok((await schedule.boundingBox()).y < (await retry.boundingBox()).y, 'The opening notice sits above the status action')
    assert.ok((await art.boundingBox()).y < (await help.boundingBox()).y, 'The artwork shares the hero and does not add another mobile panel')

    await page.evaluate(() => {
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: { writeText: () => new Promise((_, reject) => {
          window.failCopy = () => reject(new DOMException('Denied', 'NotAllowedError'))
        }) },
      })
    })
    await copy.click()
    await retry.focus()
    const recheck = page.waitForResponse(result => new URL(result.url()).pathname === '/api/stores/current/')
    await page.keyboard.press('Enter')
    await recheck
    await retry.waitFor()
    await page.evaluate(async () => {
      window.failCopy()
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
    })
    assert.equal(await address.count(), 0)
    assert.equal(await retry.evaluate(element => element === document.activeElement), true)

    await retry.click({ trial: true })
    const before = reads
    hold = Promise.withResolvers()
    await retry.focus()
    await page.keyboard.press('Enter')
    await pending.waitFor()
    assert.equal(await pending.getAttribute('aria-disabled'), 'true')
    await page.keyboard.press('Enter')
    response = { status: 503, json: { code: 503, msg: '门店服务暂不可用，请稍后重试' } }
    hold.resolve()
    await page.getByRole('alert').filter({ hasText: response.json.msg }).waitFor()
    assert.equal(reads, before + 1)
    assert.equal(await closed.count(), 0)
    assert.equal(await schedule.count(), 0)
    await art.waitFor()
    await help.waitFor()
    await copy.waitFor()
    assert.equal(await services.count(), 0)
    assert.equal(await returnGuide.count(), 0)
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

    // A delayed status response can time out, and a late response cannot enter the store.
    await page.clock.install()
    const delayed = { ...Promise.withResolvers(), handled: Promise.withResolvers() }
    hold = delayed
    const started = page.waitForRequest(request => new URL(request.url()).pathname === '/api/stores/current/')
    await page.reload()
    await started
    await pending.waitFor()
    await page.clock.fastForward(10_001)
    await page.getByRole('alert').filter({ hasText: '门店状态查询超时，请重试' }).waitFor()
    await help.waitFor()
    await copy.waitFor()
    delayed.resolve()
    await delayed.handled.promise
    assert.equal(await page.getByRole('heading', { name: '老板账号登录', exact: true }).count(), 0)
    hold = null
    await page.getByRole('button', { name: '重试', exact: true }).click()
    await page.getByRole('heading', { name: '老板账号登录', exact: true }).waitFor()
    assert.equal(page.url(), url)
  } finally {
    await context.close()
  }
}
