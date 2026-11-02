const fs=require('node:fs'),http=require('node:http'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('../.tools/club-source-20261003/node_modules/playwright');
const input=JSON.parse(fs.readFileSync(0,'utf8')),target=new URL(input.base),root=path.resolve(input.root);
const server=http.createServer((req,res)=>{
  if(req.url.startsWith('/api/')){const proxy=http.request({hostname:target.hostname,port:target.port,path:req.url,method:req.method,headers:{...req.headers,host:target.host}},response=>{res.writeHead(response.statusCode,response.headers);response.pipe(res);});proxy.on('error',()=>res.writeHead(502).end());req.pipe(proxy);return;}
  const name=decodeURIComponent(new URL(req.url,'http://localhost').pathname).replace(/^\/+/,''),file=path.resolve(root,name||'index.html');
  if(!file.startsWith(root+path.sep))return res.writeHead(404).end();
  const type={'.html':'text/html','.css':'text/css','.js':'text/javascript','.svg':'image/svg+xml'}[path.extname(file)]||'application/octet-stream';
  fs.readFile(file,(error,data)=>error?res.writeHead(404).end():res.writeHead(200,{'Content-Type':type}).end(data));
});
(async()=>{await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));let browser;
  try{
    browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
    const context=await browser.newContext({viewport:{width:375,height:812},isMobile:true,hasTouch:true});
    await context.addInitScript(({token,id})=>{localStorage.setItem('token',JSON.stringify({data:token}));localStorage.setItem('xa_provider_user',JSON.stringify({data:{id,role:'provider',nickname:'标签陪玩'}}));},input);
    const page=await context.newPage();await page.goto(`http://127.0.0.1:${server.address().port}/#/pages/points/index?claim_id=${input.claimId}`);
    await page.getByText('标签已暂停展示：内容需要重新核实',{exact:true}).waitFor({timeout:20000});
    await page.getByText('可修改后提交复核，有效期不变',{exact:false}).waitFor();
    await page.getByText('修改 TAG',{exact:true}).click();
    await page.locator('input[placeholder="希望展示的 TAG"]').fill('清海搭子');
    await page.getByText('提交复核',{exact:true}).click();
    await page.getByText('希望展示的 TAG：清海搭子',{exact:true}).waitFor({timeout:20000});
    await page.getByText('已提交修改，等待客服复核。',{exact:true}).waitFor();
    await page.reload();
    await page.getByText('已提交修改，等待客服复核。',{exact:true}).waitFor({timeout:20000});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
    await context.close();
  }finally{if(browser)await browser.close();server.closeAllConnections();server.close();}
})().catch(error=>{console.error(error.stack||error);process.exitCode=1;});
