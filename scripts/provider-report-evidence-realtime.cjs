const fs=require('node:fs'),http=require('node:http'),net=require('node:net'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('../.tools/club-source-20261003/node_modules/playwright');
const x=JSON.parse(fs.readFileSync(0,'utf8')),root=path.resolve(x.root),api=new URL(x.base),ws=new URL(x.wsBase);
const server=http.createServer((req,res)=>{if(req.url.startsWith('/api/')){const upstream=http.request({hostname:api.hostname,port:api.port,path:req.url,method:req.method,headers:{...req.headers,host:api.host}},response=>{res.writeHead(response.statusCode,response.headers);response.pipe(res)});upstream.on('error',()=>res.writeHead(502).end());req.pipe(upstream);return}const relative=decodeURIComponent(new URL(req.url,'http://localhost').pathname).replace(/^\/+/,''),file=path.resolve(root,relative||'index.html');if(!file.startsWith(root+path.sep)){res.writeHead(404).end();return}const type={'.html':'text/html','.css':'text/css','.js':'text/javascript','.png':'image/png','.svg':'image/svg+xml'}[path.extname(file)]||'application/octet-stream';fs.readFile(file,(error,data)=>error?res.writeHead(404).end():res.writeHead(200,{'Content-Type':type}).end(data))});
server.on('upgrade',(req,socket,head)=>{const upstream=net.connect(Number(ws.port),ws.hostname,()=>{const headers=req.rawHeaders.reduce((out,value,i,all)=>i%2?out:out+`${value}: ${all[i+1]}\r\n`,'');upstream.write(`${req.method} ${req.url} HTTP/${req.httpVersion}\r\n${headers}\r\n`);if(head.length)upstream.write(head);socket.pipe(upstream).pipe(socket)});upstream.on('error',()=>socket.destroy());socket.on('error',()=>upstream.destroy())});
(async()=>{await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));let browser;
  try{browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
    const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
    await context.addInitScript(({token,id})=>{localStorage.setItem('token',JSON.stringify({data:token}));localStorage.setItem('xa_provider_user',JSON.stringify({data:{id:String(id),role:'provider',nickname:'隔离陪玩',username:'隔离陪玩'}}))},{token:x.token,id:x.id});
    const page=await context.newPage();let connected;const ready=new Promise(resolve=>{connected=resolve});page.on('websocket',socket=>socket.on('framereceived',frame=>{if(String(frame.payload).includes('pong'))connected()}));
    await page.goto(`http://127.0.0.1:${server.address().port}/#/reports`);
    const card=page.locator('.report').filter({hasText:x.orderNo});await card.getByText('0/9 张').waitFor({timeout:15000});
    await Promise.race([ready,new Promise((_,reject)=>setTimeout(()=>reject(Error('websocket timeout')),10000))]);
    const reviewer=new WebSocket(`ws://${ws.host}/ws/orders/?token=${encodeURIComponent(x.reviewerToken)}`), changes=[];
    reviewer.addEventListener('message',event=>{const message=JSON.parse(event.data);if(message.type==='report_status_update'&&message.data.report_id===x.reportId)changes.push(message)});
    await new Promise((resolve,reject)=>{reviewer.addEventListener('open',resolve,{once:true});reviewer.addEventListener('error',reject,{once:true})});
    async function waitForChange(count) {for(let i=0;i<400;i++){if(changes.length>=count)return;await new Promise(resolve=>setTimeout(resolve,25))}throw Error('reviewer did not receive evidence update')}
    async function reviewerImages() {const body=await (await fetch(`${x.base}/api/admin/reports/${x.reportId}/`,{headers:{Authorization:'Bearer '+x.reviewerToken}})).json();assert.equal(body.code,0,JSON.stringify(body));return body.data.result_image_urls.length}
    const form=new FormData();form.append('kind','RESULT');form.append('image',new Blob([Buffer.from(x.image,'base64')],{type:'image/png'}),'evidence.png');
    const uploaded=await (await fetch(`${x.base}/api/wallet/reports/${x.reportId}/images/`,{method:'POST',headers:{Authorization:'Bearer '+x.token},body:form})).json();assert.equal(uploaded.code,0,JSON.stringify(uploaded));
    await card.getByText('1/9 张').waitFor({timeout:10000});
    await waitForChange(1);assert.equal(await reviewerImages(),1);
    const imageId=uploaded.data.result_images[0].id;
    const deleted=await (await fetch(`${x.base}/api/wallet/reports/${x.reportId}/images/${imageId}/`,{method:'DELETE',headers:{Authorization:'Bearer '+x.token}})).json();assert.equal(deleted.code,0,JSON.stringify(deleted));
    await card.getByText('0/9 张').waitFor({timeout:10000});
    await waitForChange(2);assert.equal(await reviewerImages(),0);reviewer.close();
  }finally{if(browser)await browser.close();server.closeAllConnections();server.close()}
})().catch(error=>{console.error(error.stack||error);process.exitCode=1;server.closeAllConnections();server.close()});
