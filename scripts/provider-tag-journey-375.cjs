const fs=require('node:fs'),http=require('node:http'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('../.tools/club-source-20261003/node_modules/playwright');
const automator=require('../.tools/wechat-automation/node_modules/miniprogram-automator');
const input=JSON.parse(fs.readFileSync(0,'utf8')),target=new URL(input.base);
function host(rootPath,admin=false){
  const root=path.resolve(rootPath);
  return http.createServer((req,res)=>{
    if(req.url.startsWith('/api/')){const proxy=http.request({hostname:target.hostname,port:target.port,path:req.url,method:req.method,headers:{...req.headers,host:target.host}},response=>{res.writeHead(response.statusCode,response.headers);response.pipe(res);});proxy.on('error',()=>res.writeHead(502).end());req.pipe(proxy);return;}
    const name=decodeURIComponent(new URL(req.url,'http://localhost').pathname).replace(/^\/+/,''), file=path.resolve(root,admin?(name.startsWith('assets/')?name:'index.html'):(name||'index.html'));
    if(!file.startsWith(root+path.sep))return res.writeHead(404).end();
    const type={'.html':'text/html','.css':'text/css','.js':'text/javascript','.svg':'image/svg+xml'}[path.extname(file)]||'application/octet-stream';
    fs.readFile(file,(error,data)=>error?res.writeHead(404).end():res.writeHead(200,{'Content-Type':type}).end(data));
  });
}
async function wait(page,predicate,label){let timer;try{await Promise.race([page.waitFor(predicate),new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error(label)),15000);})]);}finally{clearTimeout(timer);}}
(async()=>{
  const providerServer=host(input.root),adminServer=host(input.adminRoot,true);
  await Promise.all([new Promise(r=>providerServer.listen(0,'127.0.0.1',r)),new Promise(r=>adminServer.listen(0,'127.0.0.1',r))]);
  let browser,mini;
  try{
    browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
    const context=await browser.newContext({viewport:{width:375,height:812},isMobile:true,hasTouch:true});
    await context.addInitScript(({token,id})=>{localStorage.setItem('token',JSON.stringify({data:token}));localStorage.setItem('xa_provider_user',JSON.stringify({data:{id,role:'provider',nickname:'标签陪玩'}}));},{token:input.providerToken,id:input.providerId});
    const page=await context.newPage();await page.goto(`http://127.0.0.1:${providerServer.address().port}/#/pages/points/index`);
    await page.locator('input[placeholder="希望展示的 TAG"]').fill('待审核标签',{timeout:20000});
    const reward=page.locator('taro-view-core[class*="card"]').filter({hasText:'陪玩专属TAG'});
    await reward.locator('taro-button-core').filter({hasText:/^兑换$/}).click();
    await page.getByText('确定',{exact:true}).last().click();
    await page.getByText('希望展示的 TAG：待审核标签',{exact:true}).waitFor({timeout:20000});
    const headers={Authorization:`Bearer ${input.bossToken}`};
    let pub=await (await fetch(`${input.base}/api/users/escorts/${input.providerId}/`,{headers})).json();
    assert.deepEqual(pub.data.display_tags,[]);
    const claims=await (await fetch(`${input.base}/api/club/claims/?page_size=20`,{headers:{Authorization:`Bearer ${input.providerToken}`}})).json();
    const claim=claims.data.items.find(item=>item.name_snapshot==='陪玩专属TAG');assert.ok(claim);
    const rejected=await fetch(`${input.base}/api/admin/club-claims/${claim.id}/process/`,{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${input.staffToken}`},body:JSON.stringify({status:'COMPLETED',note:'审核通过'})});
    assert.equal(rejected.status,400);
    const adminContext=await browser.newContext({viewport:{width:375,height:812}});
    await adminContext.addInitScript(token=>localStorage.setItem('xa_admin_token',token),input.staffToken);
    const admin=await adminContext.newPage();await admin.goto(`http://127.0.0.1:${adminServer.address().port}/dashboard`);
    await admin.waitForURL('**/club-operations',{timeout:20000});await admin.getByRole('tab',{name:'奖品交付'}).click();
    const search=admin.getByRole('textbox',{name:'搜索权益交付记录'});
    await search.fill('待审核标签');await search.press('Enter');
    const card=admin.locator('.claim-mobile-card').filter({hasText:`记录 #${claim.id}`});
    await card.getByText('TAG：待审核标签',{exact:true}).waitFor({timeout:20000});
    await card.getByText('待审核',{exact:true}).waitFor();
    await search.clear();await search.press('Enter');
    await card.getByRole('button',{name:'处理交付'}).click({timeout:20000});
    const dialog=admin.locator('.el-dialog:visible');await dialog.locator('.el-select').click();await admin.getByRole('option',{name:'退回修改标签'}).click();
    await dialog.locator('textarea').fill('请修改标签后重新提交');await dialog.getByRole('button',{name:'保存',exact:true}).click();
    await card.getByText('待修改',{exact:true}).waitFor({timeout:20000});
    await card.getByRole('button',{name:'处理交付'}).click();
    await dialog.locator('.el-select').click();
    assert.equal(await admin.getByRole('option',{name:'交付中'}).getAttribute('aria-disabled'),'true');
    await admin.keyboard.press('Escape');await dialog.getByRole('button',{name:'关闭',exact:true}).click();
    await page.goto(`http://127.0.0.1:${providerServer.address().port}/#/pages/messages/index`);
    await page.getByText('权益交付进度更新',{exact:true}).click({timeout:20000});
    await page.waitForURL(/pages\/message-detail\/index/);
    await page.getByText('查看权益详情',{exact:true}).click();
    await page.waitForURL(/pages\/points\/index\?claim_id=/);
    await page.getByText('正在查看通知关联的权益',{exact:true}).waitFor({timeout:20000});
    const entry=page.locator('taro-view-core[class*="card"]').filter({hasText:'陪玩专属TAG'});
    await entry.getByText('请修改标签后重新提交',{exact:false}).waitFor({timeout:20000});
    await entry.getByText('请按客服要求修改 TAG 后保存。',{exact:true}).waitFor();
    await entry.locator('taro-button-core').filter({hasText:'补充交付资料'}).click();
    await page.locator('input[placeholder="希望展示的 TAG"]').fill('海洋搭子');
    await page.locator('taro-button-core:visible').filter({hasText:'保存资料'}).click();
    await page.getByText('希望展示的 TAG：海洋搭子',{exact:true}).waitFor({timeout:20000});
    await admin.getByRole('button',{name:'刷新',exact:true}).click();
    await card.getByText('待审核',{exact:true}).waitFor({timeout:20000});
    await card.getByRole('button',{name:'处理交付'}).click();
    await dialog.locator('.el-select').click();await admin.getByRole('option',{name:'已完成交付'}).click();
    await dialog.getByText('已审核标签内容符合俱乐部要求及相关规定',{exact:true}).click();
    await dialog.locator('textarea').fill('已审核并发放');await dialog.getByRole('button',{name:'保存',exact:true}).click();
    await card.getByText('已完成',{exact:true}).waitFor({timeout:20000});
    await page.goto(`http://127.0.0.1:${providerServer.address().port}/#/pages/mine/index`);
    await page.getByText('海洋搭子',{exact:true}).waitFor({timeout:20000});
    mini=await automator.connect({wsEndpoint:'ws://127.0.0.1:9435'});
    assert.equal((await mini.systemInfo()).windowWidth,375);
    await mini.evaluate(session=>require('core/auth.js').setSession(session),{token:input.bossToken,user:{id:input.bossId,role:'customer'}});
    let native=await mini.reLaunch('/pages/companions/index');
    await wait(native,async()=>(await native.data()).visible?.some(p=>p.id===input.providerId&&p.previewTags?.some(t=>t.title==='海洋搭子')),'列表未展示标签');
    await (await native.$(`.companion-card[data-id="${input.providerId}"]`)).tap();
    await wait(native,async()=>(await mini.currentPage()).path==='pages/companion-detail/index','未打开陪玩详情');
    native=await mini.currentPage();await wait(native,async()=>(await native.data()).provider?.displayTags?.some(t=>t.title==='海洋搭子'),'详情未展示标签');
    assert.ok(await native.$('.detail-tag-highlighted'));
    await adminContext.close();await context.close();
  }finally{if(mini)await mini.disconnect();if(browser)await browser.close();providerServer.closeAllConnections();providerServer.close();adminServer.closeAllConnections();adminServer.close();}
})().catch(error=>{console.error(error.stack||error);process.exitCode=1;});
