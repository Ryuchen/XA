const fs=require('node:fs'), path=require('node:path'), net=require('node:net'), assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require('../.tools/club-source-20261003/node_modules/playwright');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
(async()=>{
  const probe=net.createServer(); await new Promise(r=>probe.listen(0,'127.0.0.1',r));
  const port=probe.address().port; await new Promise(r=>probe.close(r));
  const root=path.resolve('apps/admin-web'), base=`http://127.0.0.1:${port}`;
  const vite=spawn(process.execPath,[path.join(root,'node_modules/vite/bin/vite.js'),'--host','127.0.0.1','--port',String(port),'--strictPort'],
    {cwd:root,env:{...process.env,XA_ADMIN_DEV_API_TARGET:input.api},stdio:'ignore',windowsHide:true});
  let browser;
  try {
    for(let i=0;i<120;i++){if(vite.exitCode!==null)throw Error('Vite exited');try{if((await fetch(base)).ok)break;}catch{}if(i===119)throw Error('Vite not ready');await new Promise(r=>setTimeout(r,250));}
    browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
    const context=await browser.newContext({viewport:{width:375,height:812}});
    await context.addInitScript(token=>localStorage.setItem('xa_admin_token',token),input.staffToken);
    const page=await context.newPage(); let ready; const connected=new Promise(r=>{ready=r});
    page.on('websocket',ws=>ws.on('framereceived',frame=>{if(String(frame.payload).includes('pong'))ready()}));
    await page.goto(`${base}/dashboard`,{waitUntil:'domcontentloaded'});
    const pending=page.locator('.db-work-item').filter({hasText:'待处理权益'});
    await pending.waitFor({timeout:30000});assert.match(await pending.innerText(),/0\s*条/);
    let timer;try{await Promise.race([connected,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('socket unavailable')),15000)})]);}finally{clearTimeout(timer)}
    async function post(url,token,body,method='POST'){
      const response=await fetch(input.api+'/api'+url,{method,headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'},body:JSON.stringify(body)});
      const result=await response.json();assert.equal(result.code,0,JSON.stringify(result));return result.data;
    }
    await post('/orders/gifts/send/',input.bossToken,{gift_service_id:input.giftId,quantity:1,provider_id:input.providerId,expected_amount:2100,client_request_id:'live-claim-gift'});
    await page.waitForFunction(()=>[...document.querySelectorAll('.db-work-item')].some(el=>el.textContent.includes('待处理权益')&&/1\s*条/.test(el.textContent)),null,{timeout:12000});
    await pending.click();
    const card=page.locator('.claim-mobile-card').filter({hasText:'实时队列权益'});await card.waitFor({timeout:20000});
    const id=Number((await card.innerText()).match(/记录 #(\d+)/)[1]);
    await card.getByRole('button',{name:'处理交付'}).click();
    const dialog=page.locator('.el-dialog:visible');await dialog.locator('textarea').fill('客服草稿保留');
    const refresh=page.waitForResponse(response=>response.url().includes('/api/admin/club-claims/?')&&response.status()===200,{timeout:12000});
    await post(`/club/claims/${id}/`,input.bossToken,{details:{game_uid:'live-change'}},'PATCH');
    await refresh;assert.equal(await dialog.locator('textarea').inputValue(),'客服草稿保留');
    const refreshStatus=page.waitForResponse(response=>response.url().includes('/api/admin/club-claims/?')&&response.status()===200,{timeout:12000});
    await post(`/admin/club-claims/${id}/process/`,input.staffToken,{status:'FULFILLING',note:'其他客服处理中'});
    await refreshStatus;await card.waitFor({state:'detached',timeout:12000});
    assert.equal(await dialog.locator('textarea').inputValue(),'客服草稿保留');
    await page.goto(`${base}/dashboard`);
    const fulfilling=page.locator('.db-work-item').filter({hasText:'交付中权益'});await fulfilling.waitFor({timeout:20000});assert.match(await fulfilling.innerText(),/1\s*条/);
    await post(`/admin/club-claims/${id}/process/`,input.staffToken,{status:'COMPLETED',note:'已完成'});
    await page.waitForFunction(()=>[...document.querySelectorAll('.db-work-item')].some(el=>el.textContent.includes('交付中权益')&&/0\s*条/.test(el.textContent)),null,{timeout:12000});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
    await context.close();
  }finally{if(browser)await browser.close();vite.kill()}
})().catch(error=>{console.error(error.stack||error);process.exitCode=1});
