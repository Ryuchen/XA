// Current provider UI against a real isolated API; no websocket service is
// attached, so the second provider's win must be discovered over HTTP.
const fs=require('node:fs'),http=require('node:http'),net=require('node:net'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('../.tools/club-source-20261003/node_modules/playwright');
const input=JSON.parse(fs.readFileSync(0,'utf8')),root=path.resolve(input.root),target=new URL(input.base);
const server=http.createServer((req,res)=>{
  if(req.url.startsWith('/api/')){const proxy=http.request({hostname:target.hostname,port:target.port,path:req.url,method:req.method,
    headers:{...req.headers,host:target.host,connection:'close'},agent:false},response=>{res.writeHead(response.statusCode,response.headers);response.pipe(res)});
    proxy.on('error',()=>res.writeHead(502).end());req.pipe(proxy);return;}
  const file=path.resolve(root,decodeURIComponent(new URL(req.url,'http://localhost').pathname).replace(/^\/+/, '')||'index.html');
  if(!file.startsWith(root+path.sep))return res.writeHead(404).end();
  fs.readFile(file,(error,data)=>error?res.writeHead(404).end():res.writeHead(200,{'Content-Type':{'.html':'text/html','.css':'text/css','.js':'text/javascript'}[path.extname(file)]||'application/octet-stream'}).end(data));
});
const sockets=new Set();
server.on('upgrade',(req,client,head)=>{
  if(!input.wsBase){client.destroy();return}
  const upstream=new URL(input.wsBase);sockets.add(client);client.on('close',()=>sockets.delete(client));
  const peer=net.connect(Number(upstream.port),upstream.hostname,()=>{
    const headers=req.rawHeaders.reduce((out,item,index,all)=>index%2?out:out+`${item}: ${all[index+1]}\r\n`,'');
    peer.write(`${req.method} ${req.url} HTTP/${req.httpVersion}\r\n${headers}\r\n`);if(head.length)peer.write(head);client.pipe(peer).pipe(client);
  });
  peer.on('error',()=>client.destroy());client.on('error',()=>peer.destroy());client.on('close',()=>peer.destroy());
});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  try{
    const context=await browser.newContext({viewport:{width:375,height:812},isMobile:true,hasTouch:true});
    await context.addInitScript(({token,id})=>{localStorage.setItem('token',JSON.stringify({data:token}));
      localStorage.setItem('xa_provider_user',JSON.stringify({data:{id,role:'provider',nickname:'隔离陪玩'}}));
      window.__soundCount=0;const original=AudioContext.prototype.createOscillator;
      AudioContext.prototype.createOscillator=function(){window.__soundCount++;return original.call(this)}},input);
    const page=await context.newPage(),frames=[];
    page.on('websocket',ws=>ws.on('framereceived',frame=>{try{frames.push(JSON.parse(frame.payload))}catch{}}));
    await page.goto(`http://127.0.0.1:${server.address().port}/#/orders`);
    const card=page.locator('.order-card').filter({hasText:input.orderNo});await card.waitFor({timeout:15000});
    if(input.sound){
      if(input.wsBase){for(let i=0;i<100&&!frames.some(frame=>frame.type==='pong');i++)await page.waitForTimeout(50);
        assert.ok(frames.some(frame=>frame.type==='pong'),'real websocket heartbeat');}
      const sound=page.getByRole('button',{name:'提醒声音已开'});await sound.click();
      await page.getByRole('button',{name:'提醒声音已关'}).click();
      async function create(key){const response=await fetch(`${input.base}/api/orders/create/`,{method:'POST',
        headers:{Authorization:`Bearer ${input.bossToken}`,'Content-Type':'application/json'},
        body:JSON.stringify({service_id:input.serviceId,dispatch_route:'PUBLIC',dispatch_mode:'AUTO_GRAB',client_request_id:key})});
        const result=await response.json();assert.equal(result.code,0,JSON.stringify(result));return result.data}
      const second=await create('vue-sound-second');
      await page.locator('.order-card').filter({hasText:second.order_no}).waitFor({timeout:15000});
      if(input.wsBase)assert.ok(frames.some(frame=>frame.type==='order_status_update'&&frame.data?.id===second.id),'real order notification');
      await page.waitForFunction(()=>window.__soundCount===3,null,{timeout:5000});
      await page.getByRole('button',{name:'刷新',exact:true}).click();
      await page.waitForTimeout(250);assert.equal(await page.evaluate(()=>window.__soundCount),3);
      await page.getByRole('button',{name:'提醒声音已开'}).click();
      const third=await create('vue-sound-third');
      await page.locator('.order-card').filter({hasText:third.order_no}).waitFor({timeout:15000});
      if(input.wsBase)assert.ok(frames.some(frame=>frame.type==='order_status_update'&&frame.data?.id===third.id),'muted order notification');
      assert.equal(await page.evaluate(()=>window.__soundCount),3);
      console.log('Real pool notifications sound once, and muted updates remain silent');
      await context.close();return;
    }
    await card.getByRole('button',{name:'抢单',exact:true}).click();
    const dialog=page.getByRole('dialog');await dialog.getByRole('button',{name:'确认',exact:true}).waitFor();
    let releasePool,readsAfterHeld=0;
    if(input.holdPool){
      let markHeld;
      const held=new Promise(resolve=>{markHeld=resolve}),gate=new Promise(resolve=>{releasePool=resolve});
      let captured=false;
      await page.route('**/api/orders/orders/?**',async route=>{
        const url=new URL(route.request().url());
        if(!captured&&route.request().method()==='GET'&&url.searchParams.get('status')==='pending'){
          captured=true;const response=await route.fetch();markHeld();await gate;await route.fulfill({response});
        }else {if(captured&&url.searchParams.get('status')==='pending')readsAfterHeld++;await route.continue();}
      });
      await Promise.race([held,new Promise((_,reject)=>setTimeout(()=>reject(Error('后台抢单池轮询未发出')),15000))]);
    }
    if(input.lostResponse){
      await page.route(`**/api/orders/orders/${input.orderId}/grab/`,async route=>{
        const response=await route.fetch();const result=await response.json();assert.equal(result.code,0,JSON.stringify(result));
        await route.abort('failed');
      });
    }else{
      const response=await fetch(`${input.base}/api/orders/orders/${input.orderId}/grab/`,{method:'POST',
        headers:{Authorization:`Bearer ${input.otherToken}`,'Content-Type':'application/json'},body:JSON.stringify({team_size:1})});
      const won=await response.json();assert.equal(won.code,0,JSON.stringify(won));
    }
    const rejected=input.holdPool ? page.waitForResponse(response=>response.url().includes(`/orders/${input.orderId}/grab/`)) : null;
    await dialog.getByRole('button',{name:'确认',exact:true}).click();
    if(releasePool){await rejected;await page.waitForTimeout(150);assert.equal(readsAfterHeld,0,'existing request remains coalesced');releasePool();}
    await dialog.waitFor({state:'hidden',timeout:15000});
    await card.waitFor({state:'hidden',timeout:15000});
    if(input.holdPool)assert.ok(readsAfterHeld>=1,'a new pool request must follow the delayed earlier request');
    const body=await page.locator('body').innerText();
    assert.ok(!body.includes('接单成功'),body);
    assert.ok(input.lostResponse ? body.includes('操作结果未确认，请刷新订单后核对。') : body.includes('订单状态已变化，请查看最新抢单池。'),body);
    assert.ok(!body.includes('非法状态转换'),body);
    console.log(input.lostResponse ? 'Lost success response stays uncertain and real pool refreshes' : 'Competing grab returned a failure and refreshed the real pool');
    await context.close();
  }finally{await browser.close();for(const socket of sockets)socket.destroy();server.closeAllConnections();server.close()}
})().then(()=>process.exit(0)).catch(error=>{console.error(error.stack||error);process.exit(1)});
