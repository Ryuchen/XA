const fs=require('node:fs'),path=require('node:path'),net=require('node:net'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process'),{chromium}=require('../.tools/club-source-20261003/node_modules/playwright');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
async function post(route,token,body){const response=await fetch(input.api+route,{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await response.json();assert.equal(data.code,0,JSON.stringify(data));return data.data}
(async()=>{const probe=net.createServer();await new Promise(r=>probe.listen(0,'127.0.0.1',r));const port=probe.address().port;await new Promise(r=>probe.close(r));const root=path.resolve('apps/admin-web');
const vite=spawn(process.execPath,[path.join(root,'node_modules/vite/bin/vite.js'),'--host','127.0.0.1','--port',String(port),'--strictPort'],{cwd:root,env:{...process.env,XA_ADMIN_DEV_API_TARGET:input.api},stdio:'ignore',windowsHide:true});let browser;
try{const base=`http://127.0.0.1:${port}`;for(let i=0;i<120;i++){if(vite.exitCode!==null)throw Error('vite exited');try{if((await fetch(base)).ok)break}catch{}if(i===119)throw Error('vite unavailable');await new Promise(r=>setTimeout(r,250))}
 browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});const ctx=await browser.newContext({viewport:{width:375,height:812}});await ctx.addInitScript(token=>localStorage.setItem('xa_admin_token',token),input.staffToken);
 const page=await ctx.newPage();let ready;const connected=new Promise(r=>{ready=r});page.on('websocket',ws=>ws.on('framereceived',frame=>{if(String(frame.payload).includes('pong'))ready()}));
 await page.goto(base+'/orders?find_order='+input.orderNo);const card=page.locator('.od-mobile-card').filter({hasText:input.orderNo});await card.waitFor({timeout:30000});await card.getByRole('button',{name:'查看详情'}).click();const drawer=page.locator('.el-drawer');
 const progress=()=>drawer.getByRole('button',{name:'报名进度说明'}).locator('..');await progress().filter({hasText:'0 组报名'}).waitFor();await Promise.race([connected,new Promise((_,reject)=>setTimeout(()=>reject(Error('websocket unavailable')),15000))]);
 const result=await post(`/api/orders/orders/${input.orderId}/grab/`,input.providerToken,{team_size:2,partner_id:input.partnerId});const application=result.my_applications[0];
 await progress().filter({hasText:'1 组报名 · 1 组等待队友确认'}).waitFor({timeout:15000});
 await post(`/api/orders/orders/${input.orderId}/applications/${application.id}/action/`,input.partnerToken,{action:'CONFIRM_TEAM'});
 await progress().filter({hasText:'1 组报名 · 0 组等待队友确认'}).waitFor({timeout:15000});
 await post(`/api/orders/orders/${input.orderId}/applications/${application.id}/select/`,input.bossToken,{});
 await drawer.getByRole('button',{name:'报名进度说明'}).waitFor({state:'detached',timeout:15000});await drawer.getByText('已接单',{exact:true}).first().waitFor();
 assert.equal(await drawer.evaluate(el=>el.scrollWidth<=el.clientWidth+1),true);console.log('375px admin drawer follows real team application, confirmation, and selection');
}finally{if(browser)await browser.close();vite.kill()}})().catch(e=>{console.error(e.stack||e);process.exitCode=1});
