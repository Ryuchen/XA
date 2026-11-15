// Real provider page, WebSocket and moderation endpoints against an isolated backend.
const fs=require('node:fs'),http=require('node:http'),net=require('node:net'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('../.tools/club-source-20261003/node_modules/playwright');
const input=JSON.parse(fs.readFileSync(0,'utf8')),root=path.resolve(input.root),target=new URL(input.base);
const server=http.createServer((req,res)=>{if(req.url.startsWith('/api/')){const p=http.request({hostname:target.hostname,port:target.port,path:req.url,method:req.method,headers:{...req.headers,host:target.host}},r=>{res.writeHead(r.statusCode,r.headers);r.pipe(res)});p.on('error',()=>res.writeHead(502).end());req.pipe(p);return}const file=path.resolve(root,decodeURIComponent(new URL(req.url,'http://localhost').pathname).replace(/^\/+/, '')||'index.html');if(!file.startsWith(root+path.sep))return res.writeHead(404).end();fs.readFile(file,(e,d)=>e?res.writeHead(404).end():res.writeHead(200,{'Content-Type':{'.html':'text/html','.js':'text/javascript','.css':'text/css'}[path.extname(file)]||'application/octet-stream'}).end(d))});
const sockets=new Set();server.on('upgrade',(req,client,head)=>{const u=new URL(input.wsBase);sockets.add(client);client.on('close',()=>sockets.delete(client));const peer=net.connect(Number(u.port),u.hostname,()=>{const headers=req.rawHeaders.reduce((s,item,i,all)=>i%2?s:s+`${item}: ${all[i+1]}\r\n`,'');peer.write(`${req.method} ${req.url} HTTP/${req.httpVersion}\r\n${headers}\r\n`);if(head.length)peer.write(head);client.pipe(peer).pipe(client)});peer.on('error',()=>client.destroy());client.on('error',()=>peer.destroy());client.on('close',()=>peer.destroy())});
async function post(url,token,data,method='POST'){const r=await fetch(input.base+'/api'+url,{method,headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify(data)}),v=await r.json();assert.equal(v.code,0,JSON.stringify(v));return v.data}
(async()=>{await new Promise(r=>server.listen(0,'127.0.0.1',r));const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});try{
 const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
 await context.addInitScript(({token,id})=>{localStorage.setItem('token',JSON.stringify({data:token}));localStorage.setItem('xa_provider_user',JSON.stringify({data:{id:String(id),role:'provider',nickname:'陪玩'}}))},{token:input.providerToken,id:input.providerId});
 const page=await context.newPage();let ready;const connected=new Promise(resolve=>ready=resolve);page.on('websocket',ws=>ws.on('framereceived',frame=>{try{if(JSON.parse(frame.payload).type==='pong')ready()}catch{}}));
 await page.goto(`http://127.0.0.1:${server.address().port}/#/mine`);await Promise.race([connected,new Promise((_,reject)=>setTimeout(()=>reject(Error('socket timeout')),15000))]);
 const tags=page.locator('.identity-tag');
 if(input.fallbackOnly){
  const current=async()=>tags.allTextContents(),first=['标签丁','标签丙','标签乙'],fallback=['标签丙','标签乙','标签甲'];
  await page.waitForFunction(()=>document.querySelectorAll('.identity-tag').length===3,{timeout:10000});assert.deepEqual(await current(),first);console.log('READY');
  await page.waitForFunction(expected=>JSON.stringify([...document.querySelectorAll('.identity-tag')].map(x=>x.textContent.trim()))===JSON.stringify(expected),fallback,{timeout:15000});
  if(input.syncDir) fs.writeFileSync(path.join(input.syncDir,'suspended'),'observed');
  await page.waitForFunction(expected=>JSON.stringify([...document.querySelectorAll('.identity-tag')].map(x=>x.textContent.trim()))===JSON.stringify(expected),first,{timeout:15000});
  if(input.syncDir) fs.writeFileSync(path.join(input.syncDir,'restored'),'observed');
  await page.waitForFunction(expected=>JSON.stringify([...document.querySelectorAll('.identity-tag')].map(x=>x.textContent.trim()))===JSON.stringify(expected),fallback,{timeout:30000});
  await context.close();return;
 }
 if(input.expiryOnly){await tags.getByText('海岸搭子',{exact:true}).waitFor({timeout:5000});await tags.getByText('海岸搭子',{exact:true}).waitFor({state:'hidden',timeout:15000});await context.close();return}
 assert.equal(await tags.getByText('海蓝搭子',{exact:true}).count(),0);
 await post(`/admin/club-claims/${input.claimId}/process/`,input.staffToken,{status:'COMPLETED',note:'已审核',tag_reviewed:true});await tags.getByText('海蓝搭子',{exact:true}).waitFor({timeout:15000});
 await post(`/admin/club-claims/${input.claimId}/suspend-tag/`,input.staffToken,{reason:'需重新核实',expected_tag:'海蓝搭子'});await tags.getByText('海蓝搭子',{exact:true}).waitFor({state:'hidden',timeout:15000});
 await post(`/club/claims/${input.claimId}/`,input.providerToken,{details:{tag:'海岸搭子'}},'PATCH');await post(`/admin/club-claims/${input.claimId}/review-tag/`,input.staffToken,{tag_reviewed:true,expected_tag:'海岸搭子'});await tags.getByText('海岸搭子',{exact:true}).waitFor({timeout:15000});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);await context.close();
}finally{await browser.close();for(const socket of sockets)socket.destroy();server.closeAllConnections();server.close()}})().catch(e=>{console.error(e.stack||e);process.exitCode=1});
