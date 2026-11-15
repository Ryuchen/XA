// Review an actual provider TAG in the current admin and provider applications.
const fs=require('node:fs'),http=require('node:http'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('../.tools/club-source-20261003/node_modules/playwright');
const input=JSON.parse(fs.readFileSync(0,'utf8')),target=new URL(input.base);
function host(rootPath,admin){const root=path.resolve(rootPath);return http.createServer((req,res)=>{
 if(req.url.startsWith('/api/')){const proxy=http.request({hostname:target.hostname,port:target.port,path:req.url,method:req.method,headers:{...req.headers,host:target.host}},r=>{res.writeHead(r.statusCode,r.headers);r.pipe(res)});proxy.on('error',()=>res.writeHead(502).end());req.pipe(proxy);return}
 const name=decodeURIComponent(new URL(req.url,'http://localhost').pathname).replace(/^\/+/,''),file=path.resolve(root,admin?((name.startsWith('assets/')||name==='appearance.js')?name:'index.html'):(name||'index.html'));
 if(!file.startsWith(root+path.sep))return res.writeHead(404).end();fs.readFile(file,(err,data)=>err?res.writeHead(404).end():res.writeHead(200,{'Content-Type':{'.html':'text/html','.js':'text/javascript','.css':'text/css'}[path.extname(file)]||'application/octet-stream'}).end(data));
})}
const adminServer=host(input.adminRoot,true),providerServer=host(input.providerRoot,false);
(async()=>{await Promise.all([new Promise(r=>adminServer.listen(0,'127.0.0.1',r)),new Promise(r=>providerServer.listen(0,'127.0.0.1',r))]);let browser;
 try {browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  const context=await browser.newContext({viewport:{width:1366,height:1024},hasTouch:true});await context.addInitScript(t=>localStorage.setItem('xa_admin_token',t),input.staffToken);
  const page=await context.newPage();await page.goto(`http://127.0.0.1:${adminServer.address().port}/club-operations?tab=claims`);
  const card=page.getByRole('row').filter({has:page.getByRole('cell',{name:String(input.claimId),exact:true})});await card.waitFor({timeout:20000});
  await card.getByRole('button',{name:'处理交付'}).click();const dialog=page.getByRole('dialog',{name:'配置奖品交付'});await dialog.waitFor();
  await dialog.getByLabel('处理结果').selectOption('COMPLETED');await dialog.getByText('已审核标签内容符合俱乐部要求及相关规定').click();
  await dialog.getByLabel('交付凭证 / 说明').fill('已发放');await dialog.getByRole('button',{name:'保存',exact:true}).click();await dialog.waitFor({state:'hidden',timeout:15000});
  await card.getByText(/已完成/).waitFor({timeout:10000});
  const provider=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  await provider.addInitScript(({token,id})=>{localStorage.setItem('token',JSON.stringify({data:token}));localStorage.setItem('xa_provider_user',JSON.stringify({data:{id:String(id),role:'provider',nickname:'陪玩'}}))},{token:input.providerToken,id:input.providerId});
  const pp=await provider.newPage();await pp.goto(`http://127.0.0.1:${providerServer.address().port}/#/points?claim_id=${input.claimId}`);
  const claim=pp.locator('.claim-card');try { await claim.getByText('希望展示的 TAG：海洋搭子',{exact:true}).waitFor({timeout:15000}); } catch (error) { console.error('Provider page:',(await pp.locator('body').innerText()).slice(0,1600)); throw error; } await claim.getByText('已完成',{exact:true}).waitFor();
  const response=await fetch(`${input.base}/api/users/escorts/${input.providerId}/`,{headers:{Authorization:`Bearer ${input.bossToken}`}}),body=await response.json();assert.equal(body.code,0);assert.equal(body.data.display_tags[0].title,'海洋搭子');
  const {prepareCompanion}=require('../apps/boss-miniapp/native/core/companion.js');assert.equal(prepareCompanion(body.data).previewTags[0].title,'海洋搭子');
  assert.equal(await pp.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);await provider.close();await context.close();
 }finally{if(browser)await browser.close();adminServer.closeAllConnections();adminServer.close();providerServer.closeAllConnections();providerServer.close()}
})().catch(e=>{console.error(e.stack||e);process.exitCode=1;adminServer.closeAllConnections();adminServer.close();providerServer.closeAllConnections();providerServer.close()});
