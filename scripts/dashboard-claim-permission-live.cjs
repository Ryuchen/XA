// Change the role through the real API while a workbench stays open.
const fs=require('node:fs'),http=require('node:http'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('../.tools/club-source-20261003/node_modules/playwright');
const input=JSON.parse(fs.readFileSync(0,'utf8')), target=new URL(input.base), root=path.resolve(input.root);
const server=http.createServer((req,res)=>{
  if(req.url.startsWith('/api/')) {const proxy=http.request({hostname:target.hostname,port:target.port,path:req.url,method:req.method,headers:{...req.headers,host:target.host}},response=>{res.writeHead(response.statusCode,response.headers);response.pipe(res)});proxy.on('error',()=>res.writeHead(502).end());req.pipe(proxy);return}
  const name=decodeURIComponent(new URL(req.url,'http://localhost').pathname).replace(/^\/+/,''),file=path.resolve(root,name.startsWith('assets/')?name:'index.html');
  if(!file.startsWith(root+path.sep))return res.writeHead(404).end();
  fs.readFile(file,(error,data)=>error?res.writeHead(404).end():res.writeHead(200,{'Content-Type':{'.html':'text/html','.css':'text/css','.js':'text/javascript'}[path.extname(file)]||'application/octet-stream'}).end(data));
});
(async()=>{await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));let browser;
 try {browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  const context=await browser.newContext({viewport:{width:390,height:844}});await context.addInitScript(token=>localStorage.setItem('xa_admin_token',token),input.token);
  const page=await context.newPage();await page.goto(`http://127.0.0.1:${server.address().port}/dashboard`);
  await page.getByText('开始读取：',{exact:false}).waitFor({timeout:15000});
  const item=page.locator('.db-work-item').filter({hasText:'待处理权益'});assert.equal(await item.count(),0);
  async function setPermissions(permissions) {const response=await fetch(`${input.base}/api/admin/roles/${input.roleId}/`,{method:'PATCH',headers:{Authorization:`Bearer ${input.managerToken}`,'Content-Type':'application/json'},body:JSON.stringify({permissions})});const result=await response.json();assert.equal(result.code,0,JSON.stringify(result));}
  async function refreshProfile() {await page.evaluate(async()=>{const app=document.querySelector('#app').__vue_app__;const pinia=app.config.globalProperties.$pinia;await pinia._s.get('auth').fetchProfile()});}
  await setPermissions(['dashboard:view','engagement:fulfill']);await refreshProfile();await item.waitFor({timeout:10000});assert.match(await item.innerText(),/1\s*条/);
  await setPermissions(['dashboard:view']);await refreshProfile();await item.waitFor({state:'detached',timeout:10000});
  assert.equal(await page.evaluate(()=>performance.getEntriesByType('navigation').length),1);await context.close();
 } finally {if(browser)await browser.close();server.closeAllConnections();server.close()}
})().catch(e=>{console.error(e.stack||e);process.exitCode=1;server.closeAllConnections();server.close()});
