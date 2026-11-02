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
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));let browser;
  try{
    browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
    const context=await browser.newContext({viewport:{width:375,height:812}});
    await context.addInitScript(token=>localStorage.setItem('xa_admin_token',token),input.staffToken);
    const page=await context.newPage();await page.goto(`http://127.0.0.1:${server.address().port}/club-operations`);
    await page.waitForURL('**/club-operations',{timeout:20000});await page.getByRole('tab',{name:'奖品交付'}).click();
    await page.getByText('TAG 待复核',{exact:true}).click();
    const card=page.locator('.claim-mobile-card').filter({hasText:`记录 #${input.claimId}`});
    if(input.suspended) await card.getByText('已修改待复核',{exact:true}).waitFor({timeout:20000});
    await card.getByRole('button',{name:'查看详情'}).click({timeout:20000});
    const dialog=page.locator('.el-dialog:visible');await dialog.getByText(input.suspended ? '标签已暂停展示：内容需要重新核实' : '历史标签尚无审核记录，暂不展示；请核对内容后复核。',{exact:true}).waitFor();
    assert.equal(await dialog.getByRole('button',{name:'确认审核'}).isDisabled(),true);
    await dialog.getByText('已审核标签内容符合俱乐部要求及相关规定',{exact:true}).click();
    await dialog.getByRole('button',{name:'确认审核'}).click();
    await page.getByText('暂无交付记录',{exact:true}).waitFor({timeout:20000});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
    await context.close();
  }finally{if(browser)await browser.close();server.closeAllConnections();server.close();}
})().catch(error=>{console.error(error.stack||error);process.exitCode=1;});
