const fs=require('node:fs'),http=require('node:http'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('../.tools/club-source-20261003/node_modules/playwright');
const input=JSON.parse(fs.readFileSync(0,'utf8')),root=path.resolve(input.root),target=new URL(input.base);
const endpoint=`/api/orders/orders/${input.orderId}/${input.operation==='start'?'start-service':'complete'}/`;
let count=0,observed=[];
const server=http.createServer((req,res)=>{
  if(req.url.startsWith('/api/')){const targetAction=req.method==='POST'&&req.url===endpoint;if(targetAction)count++;
    const drop=targetAction&&count===1;
    const proxy=http.request({hostname:target.hostname,port:target.port,path:req.url,method:req.method,headers:{...req.headers,host:target.host}},response=>{
      if(targetAction){let body='';response.setEncoding('utf8');response.on('data',part=>body+=part);response.on('end',()=>{observed.push(JSON.parse(body));if(drop&&input.partial){res.writeHead(200,{'Content-Type':'application/json'});res.flushHeaders();res.write('{"code":');setTimeout(()=>res.destroy(),50)}else if(drop)res.destroy();else res.writeHead(response.statusCode,response.headers).end(body)});return}
      res.writeHead(response.statusCode,response.headers);response.pipe(res)});proxy.on('error',()=>res.writeHead(502).end());req.pipe(proxy);return}
  const file=path.resolve(root,decodeURIComponent(new URL(req.url,'http://localhost').pathname).replace(/^\/+/, '')||'index.html');
  if(!file.startsWith(root+path.sep))return res.writeHead(404).end();
  fs.readFile(file,(error,data)=>error?res.writeHead(404).end():res.writeHead(200,{'Content-Type':{'.html':'text/html','.css':'text/css','.js':'text/javascript'}[path.extname(file)]||'application/octet-stream'}).end(data));
});
async function api(url,token){const res=await fetch(`${input.base}/api${url}`,{headers:{Authorization:`Bearer ${token}`}});return res.json()}
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  try{
    const context=await browser.newContext({viewport:{width:375,height:812},isMobile:true,hasTouch:true});
    await context.addInitScript(({token,id})=>{localStorage.setItem('token',JSON.stringify({data:token}));localStorage.setItem('xa_provider_user',JSON.stringify({data:{id,role:'provider',nickname:'隔离陪玩'}}))},input);
    const page=await context.newPage();await page.goto(`http://127.0.0.1:${server.address().port}/#/orders?order=${input.orderId}`);
    const card=page.locator('.order-card').filter({hasText:input.orderNo});await card.waitFor({timeout:15000});
    await card.getByRole('button',{name:input.operation==='start'?'开始服务':'完成订单',exact:true}).click();
    const dialog=page.getByRole('dialog');await dialog.locator('input[type=file]').setInputFiles(input.image);
    await dialog.getByRole('button',{name:'确认',exact:true}).click();
    if(input.fixed){
      await dialog.waitFor({state:'hidden',timeout:15000});
      if(input.partial)assert.equal(count,1,'a truncated response must remain uncertain');
      if(count===1){await page.getByText('操作结果未确认，请刷新订单后核对。',{exact:true}).waitFor()}
      else {assert.equal(count,2);assert.equal(observed[1].code,0);await page.getByText('截图已提交，请核对订单状态',{exact:true}).waitFor()}
      assert.equal(observed[0].code,0);
    }else{
      try { await dialog.getByRole('alert').waitFor({timeout:15000}) }
      catch(error){console.error('actual action responses',count,JSON.stringify(observed),'visible page',await page.locator('body').innerText());throw error}
      assert.equal(count,2,`expected browser retry, got ${count}`);assert.equal(observed[0].code,0);assert.equal(observed[1].code,400);
      assert.ok((await dialog.innerText()).includes('非法状态转换'),await dialog.innerText());
    }
    const expected=input.operation==='start'?'IN_SERVICE':'COMPLETED';
    for(const [token,prefix] of [[input.token,'/orders/orders/'],[input.bossToken,'/orders/orders/'],[input.staffToken,'/admin/orders/']]){const order=await api(`${prefix}${input.orderId}/`,token);assert.equal(order.code,0);assert.equal(order.data.status,expected)}
    console.log(`${input.operation}: first action committed; ${input.fixed ? count===1?'response remains unknown':'automatic resend recovered the same receipt' : 'automatic resend produced a rejected state transition'}`);
    await context.close();
  }finally{await browser.close();server.closeAllConnections();server.close()}
})().catch(error=>{console.error(error.stack||error);process.exitCode=1});
