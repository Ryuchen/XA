// Actual built provider page, real ASGI HTTP and WebSocket, isolated staff recharge.
const fs = require('node:fs'), http = require('node:http'), net = require('node:net'), path = require('node:path'), assert = require('node:assert/strict')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve(input.root), target = new URL(input.api)
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) { const upstream = http.request({hostname:target.hostname,port:target.port,path:req.url,method:req.method,headers:{...req.headers,host:target.host}}, response => { res.writeHead(response.statusCode,response.headers); response.pipe(res) }); upstream.on('error', () => res.writeHead(502).end()); req.pipe(upstream); return }
  const relative = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html', file = path.resolve(root, relative)
  if (!file.startsWith(root + path.sep)) { res.writeHead(404).end(); return }
  const type = {'.html':'text/html','.css':'text/css','.js':'text/javascript','.svg':'image/svg+xml','.png':'image/png'}[path.extname(file)] || 'application/octet-stream'
  fs.readFile(file, (error,data) => error ? res.writeHead(404).end() : res.writeHead(200,{'Content-Type':type}).end(data))
})
server.on('upgrade',(req,socket,head) => { const upstream = net.connect(Number(target.port),target.hostname,() => { const headers = req.rawHeaders.reduce((out,value,i,all) => i%2 ? out : out + `${value}: ${all[i+1]}\r\n`,''); upstream.write(`${req.method} ${req.url} HTTP/${req.httpVersion}\r\n${headers}\r\n`); if(head.length) upstream.write(head); socket.pipe(upstream).pipe(socket) }); upstream.on('error',() => socket.destroy()); socket.on('error',() => upstream.destroy()) })
;(async () => {
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve)); let browser
  try {
    browser = await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true})
    const context = await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true})
    await context.addInitScript(({token,id}) => { localStorage.setItem('token',JSON.stringify({data:token})); localStorage.setItem('xa_provider_user',JSON.stringify({data:{id:String(id),role:'provider',nickname:'隔离陪玩',username:'隔离陪玩'}})) }, {token:input.token,id:input.account})
    const page = await context.newPage(), errors = []; page.on('pageerror', error => errors.push(error.message))
    let connected; const ready = new Promise(resolve => { connected = resolve }); page.on('websocket', ws => ws.on('framereceived', frame => { if (String(frame.payload).includes('pong')) connected() }))
    await page.goto(`http://127.0.0.1:${server.address().port}/#/pass`); await page.getByText('钱包余额',{exact:true}).waitFor()
    await Promise.race([ready,new Promise((_,reject) => setTimeout(() => reject(Error('WebSocket timeout')),10000))])
    const balance = page.locator('.status-numbers dd').nth(1); await balance.getByText('0',{exact:false}).waitFor()
    await page.locator('.product').first().getByRole('button',{name:'采购'}).click()
    const dialog = page.getByRole('dialog'); await dialog.getByRole('button',{name:'确认采购'}).waitFor()
    const recharge = async (amount, id) => {
      const form = new FormData(); for (const [key,value] of Object.entries({account_id:input.account,amount,gift_amount:0,trade_no:id,client_request_id:id})) form.append(key,String(value))
      form.append('proof_image',new Blob([Buffer.from(input.image,'base64')],{type:'image/png'}),'proof.png')
      const response = await fetch(input.api + '/api/admin/wallets/recharge/',{method:'POST',headers:{Authorization:'Bearer '+input.staffToken},body:form})
      const result = await response.json(); assert.equal(result.code,0,JSON.stringify(result))
    }
    await recharge(100000,'pass-page-recharge')
    await balance.getByText('10000',{exact:false}).waitFor({timeout:10000})
    let release, captured, holdNext = true; const held = new Promise(resolve => { captured = resolve }), gate = new Promise(resolve => { release = resolve })
    await page.route('**/api/users/provider-pass/', async route => {
      if (route.request().method() !== 'GET' || !holdNext) return route.continue()
      holdNext = false
      const response = await route.fetch(); captured(); await gate; await route.fulfill({response})
    })
    await dialog.getByRole('button',{name:'确认采购'}).click()
    await Promise.race([held,new Promise((_,reject) => setTimeout(() => reject(Error('purchase refresh not captured')),10000))])
    await recharge(50000,'pass-page-recharge-during-purchase')
    await new Promise(resolve => setTimeout(resolve,1000))
    release(); await page.getByText('采购成功',{exact:true}).waitFor({timeout:10000})
    const latest = await (await fetch(input.api + '/api/users/provider-pass/',{headers:{Authorization:'Bearer '+input.token}})).json()
    assert.equal(latest.code,0,JSON.stringify(latest))
    await page.waitForFunction(expected => document.querySelectorAll('.status-numbers dd')[1]?.textContent?.includes(String(expected)), latest.data.balance / 10, {timeout:10000})
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),true); assert.deepEqual(errors,[])
  } finally { if(browser) await browser.close(); server.closeAllConnections(); server.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; server.closeAllConnections(); server.close() })
