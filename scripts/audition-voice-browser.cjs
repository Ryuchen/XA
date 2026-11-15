/* Real local frontend and an explicitly isolated test backend; no external traffic. */
const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('playwright')
const x = JSON.parse(fs.readFileSync(0, 'utf8'))

async function open(browser, role, identity) {
  const context = await browser.newContext({viewport:{width:390,height:844}, isMobile:true, hasTouch:true})
  const state = {failNextAudio:false, renewals:0, loseUpload:role === 'provider' && x.lose_first_upload}
  await context.addInitScript(({role, identity}) => {
    localStorage.setItem(`xa:store:shenglang:xa:fronted:${role}:session:v1`,
      JSON.stringify({token:identity.token, user:{id:identity.id, role:role === 'boss' ? 'customer' : role, nickname:'隔离测试'}, sessionId:`audition-${role}`}))
  }, {role, identity})
  await context.routeWebSocket('**/*', ws => ws.close())
  await context.route('**/*', async route => {
    const url = new URL(route.request().url())
    if (url.hostname !== 'sl.esports.club') return route.abort()
    if (!url.pathname.startsWith('/api/')) return route.continue()
    if (url.pathname.startsWith('/api/media/private/') && state.failNextAudio) {
      state.failNextAudio = false
      return route.fulfill({status:404, body:''})
    }
    if (url.pathname === '/api/media/renew/') state.renewals++
    const response = await route.fetch({url:x.api + url.pathname + url.search,
      headers:{...route.request().headers(), host:'sl.esports.club'}})
    if (state.loseUpload && url.pathname === '/api/users/recordings/' && route.request().method() === 'POST') {
      state.loseUpload = false
      return route.abort('failed')
    }
    return route.fulfill({response})
  })
  const page = await context.newPage(), errors = []
  page.on('pageerror', error => errors.push(error.message))
  return {context, page, errors, state}
}

;(async () => {
  const browser = await chromium.launch({headless:true, args:['--no-proxy-server']})
  try {
    const provider = await open(browser, 'provider', x.provider)
    await provider.page.goto(`http://sl.esports.club/player/#/orders?order=${x.order}`)
    try {
      await provider.page.getByRole('button', {name:'报名抢单'}).waitFor({timeout:15000})
    } catch (error) {
      console.error('provider page', provider.page.url(), (await provider.page.locator('body').innerText()).slice(0, 4000), provider.errors)
      throw error
    }
    console.log('provider order visible')
    await provider.page.getByRole('button', {name:'报名抢单'}).click()
    let dialog = provider.page.getByRole('dialog')
    await dialog.getByLabel('选择本人试音录音').waitFor()
    console.log('recording picker visible')
    if (x.partner) await dialog.getByLabel('选择队友').selectOption(x.partner.id)
    await dialog.getByLabel('上传新录音').setInputFiles({name:'browser-voice.wav', mimeType:'audio/wav', buffer:Buffer.from(x.audio,'base64')})
    if (x.lose_first_upload) {
      await dialog.getByRole('button', {name:'核对上传结果'}).waitFor({timeout:20000})
      // A fresh tab shares local recovery data but not the old tab's sessionStorage.
      await provider.page.close()
      provider.page = await provider.context.newPage()
      provider.page.on('pageerror', error => provider.errors.push(error.message))
      await provider.page.goto(`http://sl.esports.club/player/#/orders?order=${x.order}`)
      await provider.page.getByRole('button', {name:'报名抢单'}).click({timeout:20000})
      dialog = provider.page.getByRole('dialog')
      await dialog.getByRole('button', {name:'核对上传结果'}).click({timeout:20000})
    }
    await dialog.locator('select#audition-recording').locator('option').filter({hasText:'browser-voice'}).waitFor({state:'attached'})
    console.log('recording upload completed')
    await provider.page.waitForFunction(() => !!document.querySelector('select#audition-recording')?.value, null, {timeout:10000})
    await dialog.getByRole('button', {name:'提交报名'}).click()
    await provider.page.getByText('报名已提交', {exact:false}).first().waitFor({timeout:20000})
    console.log('provider application submitted')
    assert.deepEqual(provider.errors, [])

    let partner
    if (x.partner) {
      partner = await open(browser, 'provider', x.partner)
      await partner.page.goto('http://sl.esports.club/player/#/orders')
      await partner.page.getByRole('button', {name:'确认组队'}).first().click({timeout:20000})
      const invitation = partner.page.getByRole('dialog')
      await invitation.getByLabel('选择本人试音录音').waitFor()
      await invitation.getByLabel('上传新录音').setInputFiles({name:'partner-voice.wav', mimeType:'audio/wav', buffer:Buffer.from(x.audio,'base64')})
      await invitation.locator('select#audition-recording option').filter({hasText:'partner-voice'}).waitFor({state:'attached'})
      await partner.page.waitForFunction(() => !!document.querySelector('select#audition-recording')?.value, null, {timeout:10000})
      await invitation.getByRole('button', {name:'确认组队'}).click()
      await partner.page.getByText('组队已确认', {exact:false}).first().waitFor({timeout:20000})
      assert.deepEqual(partner.errors, [])
      console.log('partner recorded and confirmed')
    }

    const boss = await open(browser, 'boss', x.boss)
    await boss.page.goto(`http://sl.esports.club/boss/#/order-detail?id=${x.order}`)
    const audio = boss.page.locator('audio[aria-label*="报名录音"]').first()
    await audio.waitFor({timeout:30000})
    assert.equal(await boss.page.locator('audio[aria-label*="报名录音"]').count(), x.partner ? 2 : 1)
    console.log('boss audition audio visible')
    boss.state.failNextAudio = true
    await audio.evaluate(element => { element.muted = true; void element.play().catch(() => {}) })
    await boss.page.waitForFunction(() => !!document.querySelector('audio[aria-label*="报名录音"]')?.currentSrc, null, {timeout:10000})
    for (let i = 0; i < 100 && !boss.state.renewals; i++) await boss.page.waitForTimeout(100)
    assert.equal(boss.state.renewals, 1, 'failed audio playback should renew once')
    const result = await audio.evaluate(async element => {
      const response = await fetch(element.src)
      return {status:response.status, type:response.headers.get('content-type'), bytes:(await response.arrayBuffer()).byteLength}
    })
    assert.equal(result.status, 200)
    assert.match(result.type, /^audio\/wav/)
    assert.ok(result.bytes > 44)
    const range = await audio.evaluate(async element => {
      const response = await fetch(element.src, {headers:{Range:'bytes=0-43'}})
      const bytes = new Uint8Array(await response.arrayBuffer())
      return {status:response.status, contentRange:response.headers.get('content-range'),
        length:response.headers.get('content-length'), prefix:Array.from(bytes.slice(0,4))}
    })
    assert.equal(range.status, 206)
    assert.match(range.contentRange, /^bytes 0-43\/\d+$/)
    assert.equal(range.length, '44')
    assert.deepEqual(range.prefix, [82,73,70,70])
    const seeked = await audio.evaluate(async element => {
      element.load()
      element.muted = true
      await element.play()
      await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('audio seek timed out')), 5000)
        element.addEventListener('seeked', () => { clearTimeout(timer); resolve() }, {once:true})
        element.currentTime = 0.05
      })
      return element.currentTime
    })
    assert.ok(seeked >= 0.05, 'audio should seek to the requested position')
    await boss.page.getByRole('button', {name:'选择本组'}).click()
    await boss.page.getByRole('dialog').getByRole('button', {name:'确认选择'}).click()
    await boss.page.getByText('已接单', {exact:false}).first().waitFor({timeout:20000})
    assert.deepEqual(boss.errors, [])
    await provider.context.close(); if (partner) await partner.context.close(); await boss.context.close()
    process.stdout.write('provider upload and boss playback/selection verified with isolated backend\n')
  } finally { await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
