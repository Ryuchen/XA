// Real report-only reviewer page observes evidence added and removed by its provider.
const fs=require('node:fs'),http=require('node:http'),net=require('node:net'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('../.tools/club-source-20261003/node_modules/playwright');
const x=JSON.parse(fs.readFileSync(0,'utf8')),root=path.resolve(x.root),api=new URL(x.base),ws=new URL(x.wsBase);
const server=http.createServer((req,res)=>{if(req.url.startsWith('/api/')){const upstream=http.request({hostname:api.hostname,port:api.port,path:req.url,method:req.method,headers:{...req.headers,host:api.host}},response=>{res.writeHead(response.statusCode,response.headers);response.pipe(res)});upstream.on('error',()=>res.writeHead(502).end());req.pipe(upstream);return}const relative=decodeURIComponent(new URL(req.url,'http://localhost').pathname).replace(/^\/+/,''),file=path.resolve(root,relative.startsWith('assets/')?relative:'index.html');if(!file.startsWith(root+path.sep)){res.writeHead(404).end();return}const type={'.html':'text/html','.css':'text/css','.js':'text/javascript','.png':'image/png','.svg':'image/svg+xml'}[path.extname(file)]||'application/octet-stream';fs.readFile(file,(error,data)=>error?res.writeHead(404).end():res.writeHead(200,{'Content-Type':type}).end(data))});
server.on('upgrade',(req,socket,head)=>{const upstream=net.connect(Number(ws.port),ws.hostname,()=>{const headers=req.rawHeaders.reduce((out,value,i,all)=>i%2?out:out+`${value}: ${all[i+1]}\r\n`,'');upstream.write(`${req.method} ${req.url} HTTP/${req.httpVersion}\r\n${headers}\r\n`);if(head.length)upstream.write(head);socket.pipe(upstream).pipe(socket)});upstream.on('error',()=>socket.destroy());socket.on('error',()=>upstream.destroy())});
(async()=>{await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));let browser;
  try{browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
    const context=await browser.newContext({viewport:{width:1280,height:900}});await context.addInitScript(token=>localStorage.setItem('xa_admin_token',token),x.reviewerToken);
    const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));let connected;const ready=new Promise(resolve=>{connected=resolve});page.on('websocket',socket=>socket.on('framereceived',frame=>{if(String(frame.payload).includes('pong'))connected()}));
    await page.goto(`http://127.0.0.1:${server.address().port}/reports`);
    const row=page.locator('.rp-desktop-table tbody tr').filter({hasText:String(x.reportId)});await row.getByRole('button',{name:'详情'}).click({timeout:15000});
    const dialog=page.getByRole('dialog',{name:'报单详情'});await dialog.getByText('战绩截图（0 张）').waitFor({timeout:10000});
    await Promise.race([ready,new Promise((_,reject)=>setTimeout(()=>reject(Error('reviewer websocket timeout')),10000))]);
    const form=new FormData();form.append('kind','RESULT');form.append('image',new Blob([Buffer.from(x.image,'base64')],{type:'image/png'}),'evidence.png');
    const uploaded=await(await fetch(`${x.base}/api/wallet/reports/${x.reportId}/images/`,{method:'POST',headers:{Authorization:'Bearer '+x.token},body:form})).json();assert.equal(uploaded.code,0,JSON.stringify(uploaded));
    await dialog.getByText('战绩截图（1 张）').waitFor({timeout:10000});
    const deleted=await(await fetch(`${x.base}/api/wallet/reports/${x.reportId}/images/${uploaded.data.result_images[0].id}/`,{method:'DELETE',headers:{Authorization:'Bearer '+x.token}})).json();assert.equal(deleted.code,0,JSON.stringify(deleted));
    await dialog.getByText('战绩截图（0 张）').waitFor({timeout:10000});assert.deepEqual(errors,[]);
  }finally{if(browser)await browser.close();server.closeAllConnections();server.close()}
})().catch(error=>{console.error(error.stack||error);process.exitCode=1;server.closeAllConnections();server.close()});
