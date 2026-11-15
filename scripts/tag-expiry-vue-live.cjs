// Exercise real admin/provider pages and APIs across a short TAG expiry.
const fs=require('node:fs'),http=require('node:http'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('../.tools/club-source-20261003/node_modules/playwright');
const input=JSON.parse(fs.readFileSync(0,'utf8')),target=new URL(input.base);
function host(dir,admin){const root=path.resolve(dir);return http.createServer((req,res)=>{
 if(req.url.startsWith('/api/')){const p=http.request({hostname:target.hostname,port:target.port,path:req.url,method:req.method,headers:{...req.headers,host:target.host}},r=>{res.writeHead(r.statusCode,r.headers);r.pipe(res)});p.on('error',()=>res.writeHead(502).end());req.pipe(p);return}
 const name=decodeURIComponent(new URL(req.url,'http://localhost').pathname).replace(/^\/+/,''),file=path.resolve(root,admin?((name.startsWith('assets/')||name==='appearance.js')?name:'index.html'):(name||'index.html'));
 if(!file.startsWith(root+path.sep))return res.writeHead(404).end();fs.readFile(file,(e,d)=>e?res.writeHead(404).end():res.writeHead(200,{'Content-Type':{'.html':'text/html','.js':'text/javascript','.css':'text/css'}[path.extname(file)]||'application/octet-stream'}).end(d));
})}
const a=host(input.adminRoot,true),p=host(input.providerRoot,false);
(async()=>{await Promise.all([new Promise(r=>a.listen(0,'127.0.0.1',r)),new Promise(r=>p.listen(0,'127.0.0.1',r))]);let browser;
try{
 browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
 const admin=await browser.newContext({viewport:{width:1366,height:1024},hasTouch:true});await admin.addInitScript(t=>localStorage.setItem('xa_admin_token',t),input.staffToken);
 const ap=await admin.newPage();await ap.goto(`http://127.0.0.1:${a.address().port}/club-operations?tab=claims`);
 const card=ap.getByRole('row').filter({has:ap.getByRole('cell',{name:String(input.claimId),exact:true})});await card.waitFor({timeout:15000});await card.getByRole('button',{name:'查看详情'}).click();
 const ad=ap.getByRole('dialog',{name:'配置奖品交付'});await ad.getByText('标签复核',{exact:true}).waitFor();
 const provider=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,permissions:['clipboard-read','clipboard-write']});
 await provider.addInitScript(({token,id})=>{localStorage.setItem('token',JSON.stringify({data:token}));localStorage.setItem('xa_provider_user',JSON.stringify({data:{id:String(id),role:'provider',nickname:'陪玩'}}))},{token:input.providerToken,id:input.providerId});
 const pp=await provider.newPage();await pp.goto(`http://127.0.0.1:${p.address().port}/#/points?claim_id=${input.claimId}`);
 const claim=pp.locator('.claim-card');await claim.getByRole('button',{name:'修改 TAG'}).click();
 const pd=pp.getByRole('dialog');const field=pd.getByLabel('希望展示的 TAG');await field.fill('尚未保存的草稿');
 await ad.getByText('标签已到期，不再展示。').waitFor({timeout:22000});
 await pd.getByText('权益状态已变化，刚填写的资料尚未保存，可复制后联系客服。').waitFor({timeout:22000});
 assert.equal(await field.inputValue(),'尚未保存的草稿');assert.equal(await ad.getByRole('button',{name:'确认审核'}).count(),0);
 await pd.getByRole('button',{name:'复制未保存资料'}).click();await pd.getByText('资料已复制，可发给客服').waitFor();
 assert.equal(await pp.evaluate(()=>navigator.clipboard.readText()),'TAG：尚未保存的草稿');
 await provider.close();await admin.close();
}finally{if(browser)await browser.close();a.closeAllConnections();a.close();p.closeAllConnections();p.close()}
})().catch(e=>{console.error(e.stack||e);process.exitCode=1;a.closeAllConnections();a.close();p.closeAllConnections();p.close()});
