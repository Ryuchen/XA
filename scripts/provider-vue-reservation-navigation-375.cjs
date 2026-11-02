const fs=require('node:fs'),http=require('node:http'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('../.tools/club-source-20261003/node_modules/playwright');
const input=JSON.parse(fs.readFileSync(0,'utf8')),root=path.resolve(input.root),target=new URL(input.base);
const server=http.createServer((req,res)=>{if(req.url.startsWith('/api/')){const proxy=http.request({hostname:target.hostname,port:target.port,path:req.url,method:req.method,headers:{...req.headers,host:target.host}},response=>{res.writeHead(response.statusCode,response.headers);response.pipe(res)});proxy.on('error',()=>res.writeHead(502).end());req.pipe(proxy);return}const file=path.resolve(root,decodeURIComponent(new URL(req.url,'http://localhost').pathname).replace(/^\/+/, '')||'index.html');if(!file.startsWith(root+path.sep))return res.writeHead(404).end();fs.readFile(file,(error,data)=>error?res.writeHead(404).end():res.writeHead(200,{'Content-Type':{'.html':'text/html','.css':'text/css','.js':'text/javascript'}[path.extname(file)]||'application/octet-stream'}).end(data))});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  try{
    const context=await browser.newContext({viewport:{width:375,height:812},isMobile:true,hasTouch:true});
    await context.addInitScript(({token,id})=>{localStorage.setItem('token',JSON.stringify({data:token}));localStorage.setItem('xa_provider_user',JSON.stringify({data:{id,role:'provider',nickname:'隔离陪玩'}}))},input);
    const page=await context.newPage();await page.goto(`http://127.0.0.1:${server.address().port}/#/orders?order=${input.orderId}`);
    const order=page.locator('.order-card').filter({hasText:input.orderNo});await order.getByRole('button',{name:'查看预约'}).click();
    await page.locator('.reservation-no').filter({hasText:input.first.reservation_no}).waitFor({timeout:15000});
    await page.getByRole('button',{name:'查看全部预约'}).click();
    const secondTime=new Date(input.second.start_time).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
    const second=page.locator('.reservation-card').filter({hasText:secondTime});await second.getByRole('button',{name:'查看预约'}).click();
    await page.locator('.reservation-no').filter({hasText:input.second.reservation_no}).waitFor({timeout:15000});
    if(input.fixed){assert.ok(page.url().includes(`id=${input.second.id}`));await page.reload();await page.locator('.reservation-no').filter({hasText:input.second.reservation_no}).waitFor({timeout:15000});
      await page.goBack();await page.getByRole('heading',{name:'我的预约'}).waitFor({timeout:15000});await page.goBack();await page.locator('.reservation-no').filter({hasText:input.first.reservation_no}).waitFor({timeout:15000});
      await page.goForward();await page.getByRole('heading',{name:'我的预约'}).waitFor({timeout:15000});await page.goForward();await page.locator('.reservation-no').filter({hasText:input.second.reservation_no}).waitFor({timeout:15000});
      let release, heldResolve;const held=new Promise(resolve=>{heldResolve=resolve}), gate=new Promise(resolve=>{release=resolve});
      await page.route(`**/api/orders/reservations/${input.second.id}/confirm/`,async route=>{const result=await route.fetch();const body=await result.json();assert.equal(body.code,0,JSON.stringify(body));heldResolve();await gate;await route.fulfill({response:result})});
      await page.getByRole('button',{name:'确认预约',exact:true}).click();const dialog=page.getByRole('dialog');await dialog.getByRole('button',{name:'确认',exact:true}).click();await held;
      await page.goBack();await page.getByRole('heading',{name:'我的预约'}).waitFor({timeout:15000});await page.goBack();await page.locator('.reservation-no').filter({hasText:input.first.reservation_no}).waitFor({timeout:15000});
      const response=page.waitForResponse(res=>res.url().includes(`/reservations/${input.second.id}/confirm/`));release();await response;
      await page.waitForFunction(()=>{const button=[...document.querySelectorAll('button')].find(item=>item.textContent.trim()==='查看全部预约');return button&&!button.disabled},{timeout:15000});
      await page.locator('.reservation-no').filter({hasText:input.first.reservation_no}).waitFor({timeout:15000});assert.ok(!(await page.locator('body').innerText()).includes('预约已更新'));
      await page.goto(`http://127.0.0.1:${server.address().port}/#/reservations?id=${input.expired.id}`);
      await page.locator('.reservation-no').filter({hasText:input.expired.reservation_no}).waitFor({timeout:15000});await page.getByText('预约时段已结束，请与老板重新预约。',{exact:true}).waitFor();
      for(const label of ['确认预约','拒绝预约','取消预约'])assert.equal(await page.getByRole('button',{name:label,exact:true}).count(),0);
      await page.reload();await page.locator('.reservation-no').filter({hasText:input.expired.reservation_no}).waitFor({timeout:15000});await page.getByText('预约时段已结束，请与老板重新预约。',{exact:true}).waitFor();
      await page.goto(`http://127.0.0.1:${server.address().port}/#/reservations?id=${input.hidden.id}`);await page.getByRole('alert').waitFor({timeout:15000});assert.ok(!(await page.locator('body').innerText()).includes('仅指定陪玩可见的预约备注'));
      console.log('Real order -> A -> list -> B survives reload and browser back/forward');
    }else {assert.ok(page.url().includes(`id=${input.first.id}`));await page.reload();await page.locator('.reservation-no').filter({hasText:input.first.reservation_no}).waitFor({timeout:15000});console.log('REPRODUCED: opening B left A in URL and reload returned to A')}
    await context.close();
  }finally{await browser.close();server.closeAllConnections();server.close()}
})().catch(error=>{console.error(error.stack||error);process.exitCode=1});
