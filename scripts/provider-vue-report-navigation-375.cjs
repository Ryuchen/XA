const fs=require('node:fs'),http=require('node:http'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('../.tools/club-source-20261003/node_modules/playwright');
const input=JSON.parse(fs.readFileSync(0,'utf8')),root=path.resolve(input.root),target=new URL(input.base);
const server=http.createServer((req,res)=>{if(req.url.startsWith('/api/')){const proxy=http.request({hostname:target.hostname,port:target.port,path:req.url,method:req.method,headers:{...req.headers,host:target.host}},response=>{res.writeHead(response.statusCode,response.headers);response.pipe(res)});proxy.on('error',()=>res.writeHead(502).end());req.pipe(proxy);return}const file=path.resolve(root,decodeURIComponent(new URL(req.url,'http://localhost').pathname).replace(/^\/+/, '')||'index.html');if(!file.startsWith(root+path.sep))return res.writeHead(404).end();fs.readFile(file,(error,data)=>error?res.writeHead(404).end():res.writeHead(200,{'Content-Type':{'.html':'text/html','.css':'text/css','.js':'text/javascript'}[path.extname(file)]||'application/octet-stream'}).end(data))});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  try{
    const context=await browser.newContext({viewport:{width:375,height:812},isMobile:true,hasTouch:true});
    await context.addInitScript(({token,id})=>{localStorage.setItem('token',JSON.stringify({data:token}));localStorage.setItem('xa_provider_user',JSON.stringify({data:{id,role:'provider',nickname:'隔离陪玩'}}))},input);
    const page=await context.newPage();await page.goto(`http://127.0.0.1:${server.address().port}/#/messages`);
    await page.locator('.message').filter({hasText:'报单审核未通过'}).click();const message=page.getByRole('dialog');await message.getByRole('button',{name:'查看报单'}).click();
    await page.locator('.report').filter({hasText:input.first.order_no}).waitFor({timeout:15000});assert.ok(page.url().includes(`order=${input.first.id}`));
    if(input.leaveEarly==='same'){await page.getByRole('button',{name:'查看全部报单'}).click();assert.ok(!page.url().includes('order='));await page.goBack();await page.locator('.report').filter({hasText:input.first.order_no}).waitFor({timeout:15000})}
    await page.getByRole('button',{name:'从订单创建'}).click();const picker=page.getByRole('dialog');await picker.getByText(input.second.order_no,{exact:false}).click();
    if(input.leaveEarly){let release,ready;const held=new Promise(resolve=>ready=resolve),gate=new Promise(resolve=>release=resolve);
      await page.route('**/api/wallet/reports/',async route=>{if(route.request().method()!=='POST')return route.continue();const response=await route.fetch();const data=await response.json();assert.equal(data.code,0,JSON.stringify(data));ready();await gate;await route.fulfill({response})});
      await picker.getByRole('button',{name:'创建报单'}).click();await held;if(input.leaveEarly==='same'){await page.goForward();assert.ok(!page.url().includes('order='));await page.goBack()}else await page.goBack();
      if(input.leaveEarly==='same')await page.locator('.report').filter({hasText:input.first.order_no}).waitFor({timeout:15000});else await page.getByRole('heading',{name:'消息中心'}).waitFor({timeout:15000});
      const finished=page.waitForResponse(res=>res.url().endsWith('/api/wallet/reports/')&&res.request().method()==='POST');release();await finished;await page.waitForTimeout(100);
      if(input.leaveEarly==='same'){assert.ok(page.url().includes(`order=${input.first.id}`),page.url());assert.equal(await page.getByRole('dialog').count(),0);assert.equal(await page.getByText('报单已创建',{exact:true}).count(),0)}else assert.ok(page.url().includes('#/messages'),page.url());
      console.log('A pending creation does not navigate away after the user changes location');
    }else{
      await picker.getByRole('button',{name:'创建报单'}).click();await page.locator('.report').filter({hasText:input.second.order_no}).waitFor({timeout:15000});
      if(input.fixed){assert.ok(page.url().includes(`order=${input.second.id}`));await page.reload();await page.locator('.report').filter({hasText:input.second.order_no}).waitFor({timeout:15000});
        await page.goBack();await page.locator('.report').filter({hasText:input.first.order_no}).waitFor({timeout:15000});await page.goForward();await page.locator('.report').filter({hasText:input.second.order_no}).waitFor({timeout:15000});
        await page.getByRole('button',{name:'查看全部报单'}).click();await page.locator('.report').filter({hasText:input.first.order_no}).waitFor();await page.locator('.report').filter({hasText:input.second.order_no}).waitFor();assert.ok(!page.url().includes('order='));await page.reload();await page.locator('.report').filter({hasText:input.second.order_no}).waitFor();console.log('Notification A -> new report B -> all reports survives reload and history');
      }else{assert.ok(page.url().includes(`order=${input.first.id}`));await page.reload();await page.locator('.report').filter({hasText:input.first.order_no}).waitFor({timeout:15000});assert.equal(await page.locator('.report').filter({hasText:input.second.order_no}).count(),0);console.log('REPRODUCED: new report B is replaced by A after reload')}
    }
    await context.close();
  }finally{await browser.close();server.closeAllConnections();server.close()}
})().catch(error=>{console.error(error.stack||error);process.exitCode=1});
