const fs=require('node:fs'),http=require('node:http'),path=require('node:path'),assert=require('node:assert/strict');
const input=JSON.parse(fs.readFileSync(0,'utf8')),native=path.resolve(__dirname,'../apps/boss-miniapp/native');
const storage=new Map();let definition;
global.wx={getStorageSync:key=>storage.get(key),setStorageSync:(key,value)=>storage.set(key,value),getStorageInfoSync:()=>({keys:[...storage.keys()]}),removeStorageSync:key=>storage.delete(key),getAccountInfoSync:()=>({miniProgram:{envVersion:'develop'}}),showToast(){},stopPullDownRefresh(){},request(options){const url=new URL(options.url);fetch(input.base+url.pathname+url.search,{method:options.method,headers:options.header}).then(async res=>options.success({statusCode:res.status,data:await res.json()})).catch(options.fail)}};
global.Page=value=>{definition=value};
(async()=>{
  require(path.join(native,'core/auth')).setSession({token:input.bossToken,user:{id:String(input.bossId),role:'customer',nickname:'隔离老板'}});
  require(path.join(native,'pages/reservation-detail/index.js'));
  const page={...definition,data:structuredClone(definition.data),setData(patch){Object.assign(this.data,patch)}};
  for(const [key,value] of Object.entries(page))if(typeof value==='function')page[key]=value.bind(page);
  page.onLoad({id:input.reservationId});page._visible=true;await page.load();
  for(const [key,value] of Object.entries(input.fields))assert.equal(page.data.reservation[key],value);
  const wxml=fs.readFileSync(path.join(native,'pages/reservation-detail/index.wxml'),'utf8');
  for(const key of Object.keys(input.fields)){
    assert.equal(wxml.includes(`reservation.${key}`),input.fixed,`${key} owner template binding`);
    if(input.fixed)assert.ok(wxml.includes(`wx:if="{{reservation.${key}}}"`),`${key} hides an empty value`);
  }
  page.onUnload();
  if(input.root){
    const root=path.resolve(input.root),target=new URL(input.base);
    const server=http.createServer((req,res)=>{if(req.url.startsWith('/api/')){const proxy=http.request({hostname:target.hostname,port:target.port,path:req.url,method:req.method,headers:{...req.headers,host:target.host}},r=>{res.writeHead(r.statusCode,r.headers);r.pipe(res)});proxy.on('error',()=>res.writeHead(502).end());req.pipe(proxy);return}const file=path.resolve(root,decodeURIComponent(new URL(req.url,'http://localhost').pathname).replace(/^\/+/, '')||'index.html');if(!file.startsWith(root+path.sep))return res.writeHead(404).end();fs.readFile(file,(error,data)=>error?res.writeHead(404).end():res.writeHead(200,{'Content-Type':{'.html':'text/html','.css':'text/css','.js':'text/javascript'}[path.extname(file)]||'application/octet-stream'}).end(data))});
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const {chromium}=require('../.tools/club-source-20261003/node_modules/playwright');const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
    try{const context=await browser.newContext({viewport:{width:375,height:812},isMobile:true,hasTouch:true});await context.addInitScript(({providerToken,providerId})=>{localStorage.setItem('token',JSON.stringify({data:providerToken}));localStorage.setItem('xa_provider_user',JSON.stringify({data:{id:providerId,role:'provider',nickname:'隔离陪玩'}}))},input);const view=await context.newPage();await view.goto(`http://127.0.0.1:${server.address().port}/#/reservations?id=${input.reservationId}`);for(const value of Object.values(input.fields))await view.getByText(value,{exact:true}).waitFor({timeout:15000});await context.close()}finally{await browser.close();server.closeAllConnections();server.close()}
  }
  console.log(input.fixed?'Native controller carries fields; owner template binds them; current provider Vue shows them':'REPRODUCED: native controller receives all fields but owner template binds none; current provider Vue shows them');
})().catch(error=>{console.error(error.stack||error);process.exitCode=1});
