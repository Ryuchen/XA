const fs=require('node:fs'),http=require('node:http'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('../.tools/club-source-20261003/node_modules/playwright');
const input=JSON.parse(fs.readFileSync(0,'utf8')),root=path.resolve(input.root),target=new URL(input.base);
const server=http.createServer((req,res)=>{
 if(req.url.startsWith('/api/')){const p=http.request({hostname:target.hostname,port:target.port,path:req.url,method:req.method,headers:{...req.headers,host:target.host}},r=>{res.writeHead(r.statusCode,r.headers);r.pipe(res)});p.on('error',()=>{if(!res.destroyed)res.writeHead(502).end()});req.pipe(p);return}
 const name=decodeURIComponent(new URL(req.url,'http://localhost').pathname).replace(/^\/+/,''),file=path.resolve(root,(name.startsWith('assets/')||name==='appearance.js')?name:'index.html');
 if(!file.startsWith(root+path.sep))return res.writeHead(404).end();fs.readFile(file,(err,data)=>err?res.writeHead(404).end():res.writeHead(200,{'Content-Type':{'.html':'text/html','.js':'text/javascript','.css':'text/css'}[path.extname(file)]||'application/octet-stream'}).end(data));
});
(async()=>{await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});try{
 const context=await browser.newContext({viewport:{width:1366,height:1024},hasTouch:true});await context.addInitScript(token=>localStorage.setItem('xa_admin_token',token),input.token);const page=await context.newPage();
 if(input.emptyLastPage){
   await page.goto(`http://127.0.0.1:${server.address().port}/withdrawals?status=PENDING&claim_scope=unclaimed`);
   await page.getByRole('button',{name:'下一页'}).click({timeout:15000});
   const last=page.locator('.wd-desktop-table tbody tr').filter({has:page.getByRole('cell',{name:String(input.ids[10]),exact:true})});
   await last.getByRole('button',{name:'领取',exact:true}).click();
   await page.getByText('已领取，请核对收款信息后处理').waitFor({timeout:15000});
   await page.getByText('共 10 条 · 第 1 / 1 页',{exact:true}).waitFor({timeout:15000});
   assert.equal(await page.locator('.wd-desktop-table tbody tr').count(),10);
   await context.close();return;
 }
 await page.goto(`http://127.0.0.1:${server.address().port}/dashboard`);
 await page.getByRole('button',{name:/待审核提现/}).first().click({timeout:20000});
 await page.waitForURL(url=>url.pathname==='/withdrawals' && url.searchParams.get('status')==='PENDING');
 await page.getByText('按提交时间从早到晚',{exact:true}).waitFor({timeout:15000});
 const cards=page.locator('.wd-desktop-table tbody tr');await cards.nth(2).waitFor();assert.equal(await cards.count(),3);
 for(let i=0;i<3;i++)await cards.nth(i).getByRole('cell',{name:String(input.ids[i]),exact:true}).waitFor();
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
 await context.close();
}finally{await browser.close();server.closeAllConnections();server.close()}})().catch(e=>{console.error(e.stack||e);process.exitCode=1});
