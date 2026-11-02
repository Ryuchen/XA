const fs=require('node:fs'),http=require('node:http'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('../.tools/club-source-20261003/node_modules/playwright');
const input=JSON.parse(fs.readFileSync(0,'utf8')),root=path.resolve(input.root),target=new URL(input.base);
const endpoint=`/api/orders/orders/${input.orderId}/grab/`;let count=0,observed=[];
const server=http.createServer((req,res)=>{
  if(req.url.startsWith('/api/')){const action=req.method==='POST'&&req.url===endpoint;if(action)count++;const drop=action&&count===1;
    const proxy=http.request({hostname:target.hostname,port:target.port,path:req.url,method:req.method,headers:{...req.headers,host:target.host}},response=>{
      if(action){let body='';response.setEncoding('utf8');response.on('data',part=>body+=part);response.on('end',async()=>{observed.push(JSON.parse(body));if(drop&&input.changed){const rejected=await post(`/orders/orders/${input.orderId}/reject/`,input.token,{reason:'隔离回执延迟'});assert.equal(rejected.code,0,JSON.stringify(rejected));const reassigned=await post(`/orders/orders/${input.orderId}/grab/`,input.otherToken,{team_size:1});assert.equal(reassigned.code,0,JSON.stringify(reassigned))}if(drop)res.destroy();else res.writeHead(response.statusCode,response.headers).end(body)});return}
      res.writeHead(response.statusCode,response.headers);response.pipe(res)});proxy.on('error',()=>res.writeHead(502).end());req.pipe(proxy);return}
  const file=path.resolve(root,decodeURIComponent(new URL(req.url,'http://localhost').pathname).replace(/^\/+/, '')||'index.html');
  if(!file.startsWith(root+path.sep))return res.writeHead(404).end();
  fs.readFile(file,(error,data)=>error?res.writeHead(404).end():res.writeHead(200,{'Content-Type':{'.html':'text/html','.css':'text/css','.js':'text/javascript'}[path.extname(file)]||'application/octet-stream'}).end(data));
});
async function api(url,token){return fetch(`${input.base}/api${url}`,{headers:{Authorization:`Bearer ${token}`}}).then(r=>r.json())}
async function post(url,token,data){return fetch(`${input.base}/api${url}`,{method:'POST',headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'},body:JSON.stringify(data)}).then(r=>r.json())}
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  try{
    const context=await browser.newContext({viewport:{width:375,height:812},isMobile:true,hasTouch:true});
    await context.addInitScript(({token,id})=>{localStorage.setItem('token',JSON.stringify({data:token}));localStorage.setItem('xa_provider_user',JSON.stringify({data:{id,role:'provider',nickname:'隔离陪玩'}}))},input);
    const page=await context.newPage();await page.goto(`http://127.0.0.1:${server.address().port}/#/orders`);
    const card=page.locator('.order-card').filter({hasText:input.orderNo});await card.waitFor({timeout:15000});await card.getByRole('button',{name:'抢单',exact:true}).click();
    const dialog=page.getByRole('dialog');await dialog.getByRole('button',{name:'确认',exact:true}).click();await dialog.waitFor({state:'hidden',timeout:15000});
    assert.equal(observed[0].code,0);assert.ok(count===1||count===2);
    if(count===2){assert.equal(observed[1].code,input.fixed?0:400);await page.getByText(input.fixed?(input.changed?'本次请求已处理，请核对订单最新状态。':'接单成功'):'订单状态已变化，请查看最新抢单池。',{exact:true}).waitFor()}
    else await page.getByText('操作结果未确认，请刷新订单后核对。',{exact:true}).waitFor();
    for(const [token,prefix] of [[input.token,'/orders/orders/'],[input.bossToken,'/orders/orders/'],[input.staffToken,'/admin/orders/']]){const order=await api(`${prefix}${input.orderId}/`,token);assert.equal(order.code,0);assert.equal(order.data.status,'GRABBED')}
    console.log(count===2?(input.fixed?'automatic retry recovered the same grab':'REPRODUCED: own grab succeeded, retry was shown as a changed order'):'committed grab, no browser retry; UI kept result uncertain');
    await context.close();
  }finally{await browser.close();server.closeAllConnections();server.close()}
})().catch(error=>{console.error(error.stack||error);process.exitCode=1});
