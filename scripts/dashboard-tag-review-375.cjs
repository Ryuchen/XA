const fs=require('node:fs'),http=require('node:http'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('../.tools/club-source-20261003/node_modules/playwright');
const input=JSON.parse(fs.readFileSync(0,'utf8')),target=new URL(input.base),root=path.resolve(input.root);
const server=http.createServer((req,res)=>{
  if(req.url.startsWith('/api/')){const proxy=http.request({hostname:target.hostname,port:target.port,path:req.url,method:req.method,headers:{...req.headers,host:target.host}},response=>{res.writeHead(response.statusCode,response.headers);response.pipe(res);});proxy.on('error',()=>res.writeHead(502).end());req.pipe(proxy);return;}
  const name=decodeURIComponent(new URL(req.url,'http://localhost').pathname).replace(/^\/+/,''),file=path.resolve(root,name.startsWith('assets/')?name:'index.html');
  if(!file.startsWith(root+path.sep))return res.writeHead(404).end();
  const type={'.html':'text/html','.css':'text/css','.js':'text/javascript','.svg':'image/svg+xml'}[path.extname(file)]||'application/octet-stream';
  fs.readFile(file,(error,data)=>error?res.writeHead(404).end():res.writeHead(200,{'Content-Type':type}).end(data));
});
(async()=>{await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));let browser;
  try{
    browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
    const context=await browser.newContext({viewport:{width:375,height:812}});
    await context.addInitScript(token=>localStorage.setItem('xa_admin_token',token),input.staffToken);
    const page=await context.newPage();await page.goto(`http://127.0.0.1:${server.address().port}/dashboard`);
    const shortcut=page.getByRole('button',{name:input.ready ? /待审核 TAG/ : /待复核 TAG/});await shortcut.waitFor({timeout:20000});
    assert.match(await shortcut.innerText(),/1\s*条/);await shortcut.click();
    await page.waitForURL('**/club-operations?**',{timeout:20000});
    const card=page.locator('.claim-mobile-card').filter({hasText:`记录 #${input.claimId}`});await card.waitFor({timeout:20000});
    assert.equal(await page.locator('.claim-mobile-card').count(),1);
    if(input.ready){await card.getByText('待审核',{exact:true}).waitFor(); assert.ok(page.url().includes('tag_ready_for_review=1'));}
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
    await context.close();
  }finally{if(browser)await browser.close();server.closeAllConnections();server.close();}
})().catch(error=>{console.error(error.stack||error);process.exitCode=1;});
