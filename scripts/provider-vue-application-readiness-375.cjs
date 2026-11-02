const fs=require('node:fs'),http=require('node:http'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('../.tools/club-source-20261003/node_modules/playwright');
const input=JSON.parse(fs.readFileSync(0,'utf8')),root=path.resolve(input.root),target=new URL(input.base);
const server=http.createServer((req,res)=>{
  if(req.url.startsWith('/api/')){const proxy=http.request({hostname:target.hostname,port:target.port,path:req.url,method:req.method,headers:{...req.headers,host:target.host}},response=>{res.writeHead(response.statusCode,response.headers);response.pipe(res)});proxy.on('error',()=>res.writeHead(502).end());req.pipe(proxy);return}
  const file=path.resolve(root,decodeURIComponent(new URL(req.url,'http://localhost').pathname).replace(/^\/+/, '')||'index.html');
  if(!file.startsWith(root+path.sep))return res.writeHead(404).end();
  fs.readFile(file,(error,data)=>error?res.writeHead(404).end():res.writeHead(200,{'Content-Type':{'.html':'text/html','.css':'text/css','.js':'text/javascript'}[path.extname(file)]||'application/octet-stream'}).end(data));
});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  try{
    if(input.poolState){
      const context=await browser.newContext({viewport:{width:375,height:812},isMobile:true,hasTouch:true});
      await context.addInitScript(({token,id})=>{localStorage.setItem('token',JSON.stringify({data:token}));localStorage.setItem('xa_provider_user',JSON.stringify({data:{id,role:'provider',nickname:'隔离陪玩'}}))},input.provider);
      const page=await context.newPage();await page.goto(`http://127.0.0.1:${server.address().port}/#/orders`);
      const card=page.locator('.order-card').filter({hasText:input.orderNo});await card.waitFor({timeout:15000});
      if(input.poolState==='withdrawn'){assert.equal(await card.getByRole('button',{name:'查看报名状态'}).count(),0);assert.equal(await card.getByRole('button',{name:'报名抢单'}).count(),1)}
      else {assert.ok((await card.innerText()).includes(input.poolState==='single'?'报名已提交':'等待队友确认'));assert.equal(await card.getByRole('button',{name:'确认组队'}).count(),input.isPartner?1:0);await card.getByRole('button',{name:'查看报名状态'}).click();assert.ok(page.url().includes(`#/applications/${input.orderId}?application=${input.applicationId}`));await page.locator('.order-no').filter({hasText:input.orderNo}).waitFor({timeout:15000})}
      await context.close();return;
    }
    const online=input.providers[1-input.offlineIndex], poolContext=await browser.newContext({viewport:{width:375,height:812},isMobile:true,hasTouch:true});
    await poolContext.addInitScript(({token,id})=>{localStorage.setItem('token',JSON.stringify({data:token}));localStorage.setItem('xa_provider_user',JSON.stringify({data:{id,role:'provider',nickname:'隔离陪玩'}}))},online);
    const poolPage=await poolContext.newPage();await poolPage.goto(`http://127.0.0.1:${server.address().port}/#/orders`);
    const card=poolPage.locator('.order-card').filter({hasText:input.orderNo});await card.waitFor({timeout:15000});
    if(input.expectFixed){await card.getByRole('button',{name:'查看报名状态'}).click();await poolPage.getByText('队伍已确认，当前暂不可供老板选择',{exact:true}).waitFor({timeout:15000})}
    else {assert.ok((await card.innerText()).includes('等待老板选择'));assert.equal(await card.getByRole('button',{name:'查看报名状态'}).count(),0)}
    await poolContext.close();
    if(input.poolOnly){console.log('Existing pool card says waiting and has no application detail entry');return}
    const pages=[];
    for(const [index,provider] of input.providers.entries()){
      const context=await browser.newContext({viewport:{width:375,height:812},isMobile:true,hasTouch:true});
      await context.addInitScript(({token,id})=>{localStorage.setItem('token',JSON.stringify({data:token}));localStorage.setItem('xa_provider_user',JSON.stringify({data:{id,role:'provider',nickname:'隔离陪玩'}}))},provider);
      const page=await context.newPage();
      await page.goto(`http://127.0.0.1:${server.address().port}/#/applications/${input.orderId}?application=${input.applicationId}`);
      await page.locator('.order-no').filter({hasText:input.orderNo}).waitFor({timeout:15000});
      const text=await page.locator('body').innerText();
      if(!input.expectFixed){assert.ok(text.includes('队伍已确认，等待老板选择'),text);assert.ok(!text.includes('离线'),text)}
      else {
        assert.ok(text.includes('队伍已确认，当前暂不可供老板选择'),text);
        assert.ok(index===input.offlineIndex ? text.includes('陪玩当前处于离线状态') : text.includes('队友当前暂不符合接单条件'),text);
        if(index!==input.offlineIndex)assert.ok(!text.includes('离线'),text);
        const response=await fetch(`${input.base}/api/orders/orders/${input.orderId}/applications/${input.applicationId}/mine/`,{headers:{Authorization:`Bearer ${provider.token}`}});
        const detail=await response.json();assert.equal(detail.code,0,JSON.stringify(detail));assert.equal(detail.data.application.ready,true);assert.equal(detail.data.selectable,false);
      }
      console.log(`provider ${index}: ${text.replace(/\s+/g,' ').slice(0,240)}`);
      pages.push({page,context});
    }
    if(input.expectFixed){
      const restored=await fetch(`${input.base}/api/users/escorts/status/`,{method:'POST',headers:{Authorization:`Bearer ${input.providers[input.offlineIndex].token}`,'Content-Type':'application/json'},body:JSON.stringify({status:'AVAILABLE'})}).then(r=>r.json());assert.equal(restored.code,0,JSON.stringify(restored));
      for(const {page} of pages){await page.getByRole('button',{name:'刷新报名状态'}).click();await page.getByText('队伍已确认，等待老板选择',{exact:true}).waitFor({timeout:15000})}
      const boss=await fetch(`${input.base}/api/orders/orders/${input.orderId}/`,{headers:{Authorization:`Bearer ${input.bossToken}`}}).then(r=>r.json());assert.equal(boss.code,0);assert.equal(boss.data.applications.find(row=>row.id===input.applicationId).ready,true);
      const selected=await fetch(`${input.base}/api/orders/orders/${input.orderId}/applications/${input.applicationId}/select/`,{method:'POST',headers:{Authorization:`Bearer ${input.bossToken}`,'Content-Type':'application/json'},body:'{}'}).then(r=>r.json());assert.equal(selected.code,0,JSON.stringify(selected));
      const staff=await fetch(`${input.base}/api/admin/orders/${input.orderId}/`,{headers:{Authorization:`Bearer ${input.staffToken}`}}).then(r=>r.json());assert.equal(staff.code,0);assert.equal(staff.data.status,'GRABBED');
      for(const {page} of pages){await page.getByRole('button',{name:'刷新报名状态'}).click();await page.getByText('本次报名已选中，请查看订单状态。',{exact:true}).waitFor({timeout:15000})}
      console.log('Restored eligibility was refreshed by both members, then the boss selected the actual team');
    }
    for(const {context} of pages)await context.close();
  }finally{await browser.close();server.closeAllConnections();server.close()}
})().catch(error=>{console.error(error.stack||error);process.exitCode=1});
