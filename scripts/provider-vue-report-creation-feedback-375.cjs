const fs=require('node:fs'),http=require('node:http'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('../.tools/club-source-20261003/node_modules/playwright');
const input=JSON.parse(fs.readFileSync(0,'utf8')),root=path.resolve(input.root),target=new URL(input.base);
const server=http.createServer((req,res)=>{
  if(req.url.startsWith('/api/')){const proxy=http.request({hostname:target.hostname,port:target.port,path:req.url,method:req.method,headers:{...req.headers,host:target.host}},response=>{res.writeHead(response.statusCode,response.headers);response.pipe(res)});proxy.on('error',()=>res.writeHead(502).end());req.pipe(proxy);return}
  const file=path.resolve(root,decodeURIComponent(new URL(req.url,'http://localhost').pathname).replace(/^\/+/, '')||'index.html');
  if(!file.startsWith(root+path.sep))return res.writeHead(404).end();
  fs.readFile(file,(error,data)=>error?res.writeHead(404).end():res.writeHead(200,{'Content-Type':{'.html':'text/html','.css':'text/css','.js':'text/javascript'}[path.extname(file)]||'application/octet-stream'}).end(data));
});
async function api(url,token,options={}){const res=await fetch(`${input.base}/api${url}`,{...options,headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'}});return res.json()}
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  try{
    const context=await browser.newContext({viewport:{width:375,height:812},isMobile:true,hasTouch:true});
    await context.addInitScript(({token,id})=>{localStorage.setItem('token',JSON.stringify({data:token}));localStorage.setItem('xa_provider_user',JSON.stringify({data:{id,role:'provider',nickname:'隔离陪玩'}}))},input);
    const page=await context.newPage();await page.goto(`http://127.0.0.1:${server.address().port}/#/reports`);
    await page.getByRole('button',{name:'从订单创建'}).click();
    const dialog=page.getByRole('dialog');await dialog.getByText(input.orderNo,{exact:false}).click();
    const rejected=await api(`/orders/orders/${input.orderId}/reject/`,input.token,{method:'POST',body:JSON.stringify({reason:'隔离测试时间冲突'})});assert.equal(rejected.code,0,JSON.stringify(rejected));
    for(const [token,prefix] of [[input.bossToken,'/orders/orders/'],[input.staffToken,'/admin/orders/']]){const order=await api(`${prefix}${input.orderId}/`,token);assert.equal(order.code,0);assert.equal(order.data.status,'PENDING')}
    const responsePromise=page.waitForResponse(res=>res.url().endsWith('/api/wallet/reports/')&&res.request().method()==='POST');
    await dialog.getByRole('button',{name:'创建报单'}).click();const response=await responsePromise;const failure=await response.json();assert.equal(failure.code,400,JSON.stringify(failure));
    if(input.fixed){
      await dialog.getByText(failure.msg,{exact:true}).waitFor({timeout:5000});
      await dialog.getByRole('button',{name:'取消'}).click();await dialog.waitFor({state:'hidden'});
      const second=await api('/orders/create/',input.bossToken,{method:'POST',body:JSON.stringify({service_id:input.serviceId,provider_id:input.id,client_request_id:'report-picker-recovery'})});assert.equal(second.code,0,JSON.stringify(second));
      await page.getByRole('button',{name:'从订单创建'}).click();await dialog.getByText(second.data.order_no,{exact:false}).click();assert.equal(await dialog.getByText(failure.msg,{exact:true}).count(),0);
      await page.route('**/api/wallet/reports/',async route=>{if(route.request().method()!=='POST')return route.continue();const sent=await route.fetch();const body=await sent.json();assert.equal(body.code,0,JSON.stringify(body));await route.abort('failed')});
      await dialog.getByRole('button',{name:'创建报单'}).click();await dialog.waitFor({state:'hidden',timeout:15000});
      await page.getByText('创建结果未确认，请核对报单',{exact:true}).waitFor({timeout:15000});
      const reports=await api('/wallet/reports/',input.token);assert.equal(reports.code,0);assert.ok(reports.data.some(row=>row.order_id===second.data.id));
      console.log('Visible rejection; another real order succeeds despite its response being lost, and UI keeps the result uncertain');
    }
    else {await page.waitForFunction(message=>document.body.textContent.includes(message),failure.msg);assert.equal(await dialog.getByText(failure.msg,{exact:true}).count(),0);console.log('Real rejection exists only outside the still-open modal:',failure.msg)}
    await context.close();
  }finally{await browser.close();server.closeAllConnections();server.close()}
})().catch(error=>{console.error(error.stack||error);process.exitCode=1});
