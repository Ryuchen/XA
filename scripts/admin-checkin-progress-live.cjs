// Built admin page, real boss check-in and separate ASGI processes over Redis.
const fs=require('node:fs'), http=require('node:http'), net=require('node:net'), path=require('node:path'), assert=require('node:assert/strict')
const {chromium}=require('../.tools/club-source-20261003/node_modules/playwright')
const input=JSON.parse(fs.readFileSync(0,'utf8')), root=path.resolve(input.root), api=new URL(input.base), ws=new URL(input.wsBase)
const server=http.createServer((req,res) => {
  if(req.url.startsWith('/api/')) {const upstream=http.request({hostname:api.hostname,port:api.port,path:req.url,method:req.method,headers:{...req.headers,host:api.host}}, response => {res.writeHead(response.statusCode,response.headers);response.pipe(res)});upstream.on('error',() => res.writeHead(502).end());req.pipe(upstream);return}
  const relative=decodeURIComponent(new URL(req.url,'http://localhost').pathname).replace(/^\/+/,''), file=path.resolve(root,relative.startsWith('assets/')?relative:'index.html')
  if(!file.startsWith(root+path.sep)){res.writeHead(404).end();return}
  const type={'.html':'text/html','.css':'text/css','.js':'text/javascript','.svg':'image/svg+xml','.png':'image/png'}[path.extname(file)]||'application/octet-stream'
  fs.readFile(file,(error,data) => error?res.writeHead(404).end():res.writeHead(200,{'Content-Type':type}).end(data))
})
server.on('upgrade',(req,socket,head) => {const upstream=net.connect(Number(ws.port),ws.hostname,() => {const headers=req.rawHeaders.reduce((out,value,i,all) => i%2?out:out+`${value}: ${all[i+1]}\r\n`,'');upstream.write(`${req.method} ${req.url} HTTP/${req.httpVersion}\r\n${headers}\r\n`);if(head.length)upstream.write(head);socket.pipe(upstream).pipe(socket)});upstream.on('error',() => socket.destroy());socket.on('error',() => upstream.destroy())})
;(async () => {
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve)); let browser
  try {
    browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true})
    const context=await browser.newContext({viewport:{width:1280,height:900}})
    await context.addInitScript(token => localStorage.setItem('xa_admin_token',token),input.staffToken)
    const page=await context.newPage(), errors=[];page.on('pageerror',error => errors.push(error.message))
    let connected;const ready=new Promise(resolve => {connected=resolve});page.on('websocket',socket => socket.on('framereceived',frame => {if(String(frame.payload).includes('pong'))connected()}))
    await page.goto(`http://127.0.0.1:${server.address().port}/checkin`)
    const row=page.locator('tr').filter({hasText:input.username});await row.getByText('已签到 0 天').waitFor({timeout:15000})
    await Promise.race([ready,new Promise((_,reject) => setTimeout(() => reject(Error('admin websocket timeout')),10000))])
    const rule=await (await fetch(input.base+'/api/admin/config/checkin',{method:'PUT',headers:{Authorization:'Bearer '+input.staffToken,'Content-Type':'application/json'},body:JSON.stringify({makeup_card_spend_required:18800})})).json()
    assert.equal(rule.code,0,JSON.stringify(rule))
    const calendar=await (await fetch(input.base+'/api/users/checkin/',{headers:{Authorization:'Bearer '+input.bossToken}})).json()
    assert.equal(calendar.code,0,JSON.stringify(calendar));assert.equal(calendar.data.makeup_cards,1)
    await row.getByText('补签卡 1 张').waitFor({timeout:10000})
    let expected=1
    if(calendar.data.makeup_available_days.length) {
      const makeup=await (await fetch(input.base+'/api/users/checkin/',{method:'POST',headers:{Authorization:'Bearer '+input.bossToken,'Content-Type':'application/json'},
        body:JSON.stringify({day:calendar.data.makeup_available_days[0]})})).json()
      assert.equal(makeup.code,0,JSON.stringify(makeup));assert.equal(makeup.data.makeup_cards,0)
      await row.getByText('已签到 1 天').waitFor({timeout:10000})
      await row.getByText('补签卡 0 张').waitFor({timeout:10000});expected=2
    }
    const response=await fetch(input.base+'/api/users/checkin/',{method:'POST',headers:{Authorization:'Bearer '+input.bossToken,'Content-Type':'application/json'},body:'{}'})
    const result=await response.json();assert.equal(result.code,0,JSON.stringify(result))
    await row.getByText(`已签到 ${expected} 天`).waitFor({timeout:10000});assert.deepEqual(errors,[])
  } finally {if(browser)await browser.close();server.closeAllConnections();server.close()}
})().catch(error => {console.error(error.stack||error);process.exitCode=1;server.closeAllConnections();server.close()})
