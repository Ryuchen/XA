const fs=require('node:fs'),http=require('node:http'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('../.tools/club-source-20261003/node_modules/playwright');
const input=JSON.parse(fs.readFileSync(0,'utf8')),root=path.resolve(input.root),target=new URL(input.base);
let uploadCount=0;
const server=http.createServer((req,res)=>{
  if(req.url.startsWith('/api/')){const drop=['lost','partial'].includes(input.scenario)&&req.method==='POST'&&req.url===`/api/wallet/reports/${input.reportId}/images/`&&++uploadCount===2;
    const proxy=http.request({hostname:target.hostname,port:target.port,path:req.url,method:req.method,headers:{...req.headers,host:target.host}},response=>{
      if(drop){let body='';response.setEncoding('utf8');response.on('data',part=>body+=part);response.on('end',()=>{if(JSON.parse(body).code!==0)console.error('actual upload rejected',body);if(input.scenario==='partial'){res.writeHead(200,{'Content-Type':'application/json'});res.flushHeaders();res.write('{"code":');setTimeout(()=>res.destroy(),50)}else res.destroy()});return}
      res.writeHead(response.statusCode,response.headers);response.pipe(res)});proxy.on('error',()=>res.writeHead(502).end());req.pipe(proxy);return}
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
    const card=page.locator('.report').filter({hasText:input.orderNo});await card.waitFor({timeout:15000});
    const upload=card.locator('input[type=file][multiple]');
    if(input.scenario==='limit'){
      await upload.setInputFiles(input.pictures);await card.getByText('9/9 张',{exact:true}).waitFor({timeout:15000});
      if(input.fixed)await page.getByText('已上传 9 张，另有 1 张超出上限未上传。',{exact:true}).waitFor();
      else {await page.getByText('截图已上传',{exact:true}).waitFor();console.log('Reproduced: selected 10, uploaded 9, no omitted-file warning')}
    }else if(input.scenario==='invalid'){
      await upload.setInputFiles([input.pictures[0],input.invalid,input.pictures[1]]);await card.getByText('1/9 张',{exact:true}).waitFor({timeout:15000});
      if(input.fixed){await page.getByText('已上传 1 张；请上传有效的截图文件，后续图片未上传。',{exact:true}).waitFor();await upload.setInputFiles(input.pictures[1]);await card.getByText('2/9 张',{exact:true}).waitFor()}
      else {await page.getByText('请上传有效的截图文件',{exact:true}).waitFor();console.log('Reproduced: first image saved but error omits partial outcome')}
    }else if(input.scenario==='partial'){
      await upload.setInputFiles(input.pictures.slice(0,3));
      await card.getByText('2/9 张',{exact:true}).waitFor({timeout:15000});
      await card.getByText('已确认上传 1 张；当前图片上传结果未确认，后续图片未上传，请核对截图。',{exact:true}).waitFor();
      await upload.setInputFiles(input.pictures[2]);await card.getByText('3/9 张',{exact:true}).waitFor();
    }else{
      await upload.setInputFiles(input.pictures.slice(0,3));await card.getByText('3/9 张',{exact:true}).waitFor({timeout:15000});
      await card.getByText('截图已上传',{exact:true}).waitFor();assert.equal(uploadCount,4,'browser resent one committed upload');
    }
    if(input.fixed){
      await card.getByRole('button',{name:'提交审核'}).click();await card.getByText('待审核',{exact:true}).waitFor({timeout:15000});
      const staff=await api(`/admin/reports/${input.reportId}/`,input.staffToken);assert.equal(staff.code,0,JSON.stringify(staff));assert.equal(staff.data.result_image_urls.length,input.scenario==='limit'?9:['lost','partial'].includes(input.scenario)?3:2);
      const boss=await api(`/orders/orders/${input.orderId}/`,input.bossToken);assert.equal(boss.code,0);assert.equal(boss.data.status,'COMPLETED');
      console.log('Real uploaded files submitted and visible to staff; boss order remains completed');
    }
    await context.close();
  }finally{await browser.close();server.closeAllConnections();server.close()}
})().catch(error=>{console.error(error.stack||error);process.exitCode=1});
