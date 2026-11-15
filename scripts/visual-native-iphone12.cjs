// Read-only visual review in the running WeChat developer tools.
// Fixture injection is for screenshots, never a business acceptance test.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
if(process.env.XA_WECHAT_WINDOW_MAXIMIZED!=='1')throw new Error('请先在微信开发者工具中最大化模拟器窗口，确认整屏可见后设置 XA_WECHAT_WINDOW_MAXIMIZED=1 再截图。');
const automator=require('../.tools/wechat-automation/node_modules/miniprogram-automator');
const fixtureDir=path.resolve('design-demos/native-pages/fixtures');
const output=path.resolve(process.env.XA_VISUAL_OUTPUT||'design-demos/native-iphone12-review-20261103');
const pages=['home','companions','companion-detail','checkout','orders','catalog','service','companion-picker','audition','club-content','order-detail','reservations','reservation-create','reservation-detail','support','wallet','points','coupons','membership','gifts','checkin','lottery','reward-claim','achievements','rankings','messages','message-detail','announcements','announcement-detail','chat','mine','login','profile','security','favorites'];
const faces=['小鹿','阿泽','林一','小满'].map((name,i)=>({id:100+i,nickname:name,initial:name.slice(0,1),level_name:'王牌陪玩',gender:i%2?'MALE':'FEMALE',monthlyAmountText:'1,268',can_order:true,previewTags:[]}));
const order={id:1,order_no:'DEMO-XA-001',status:'PENDING',statusText:'待接单',payment_status:'PAID',service:{name:'无畏契约 · 排位陪玩'},amountText:'358',providerDisplay:'平台安排',quantityText:'2 小时',refundRemainingText:''};
const extraFixtures={
 companions:{visible:faces,categories:[{id:1,name:'无畏契约'},{id:2,name:'英雄联盟'}]},
 'companion-picker':{visible:faces,serviceId:101,serviceName:'无畏契约 · 排位陪玩',selectedProviders:[],selectionLimit:1},
 orders:{items:[order]},
 messages:{section:'private',unread:2,items:[{id:1,is_read:false,typeText:'订单',title:'陪玩已接单',preview:'您的订单已确认，请准备开始游戏。',dateText:'今天 10:24'},{id:2,is_read:false,typeText:'预约',title:'预约时间已确认',preview:'请在预约时间前确认游戏资料。',dateText:'昨天 19:30'}],feedError:'',feedLoading:false,feed:[{id:1,kind:'REVIEW',label:'俱乐部动态',time:'今天',content:'示例动态：感谢陪玩认真完成服务。',icon:'heart'}]},
 announcements:{items:[{id:1,title:'俱乐部服务安排说明',is_pinned:true,dateText:'2026-11-03'}]},
 'announcement-detail':{item:{title:'俱乐部服务安排说明',is_pinned:true,dateText:'2026-11-03',content:'示例内容：请在下单前核对服务说明。'}},
 chat:{items:[{id:1,is_from_support:true,content:'您好，请问有什么可以帮您？',time:'10:00'}],historyError:'',contextLoading:false},
 mine:{profile:{id:'DEMO',nickname:'演示老板',isWechatBound:true},balance:'1,268',stats:{pending:1,grabbed:0,in_service:1},member:{vip:{name:'VIP3'},points:1280,display_tags:[]}}
};
const bottom=process.argv.includes('--bottom'),requested=process.argv.slice(2).filter(x=>x!=='--bottom'),targets=requested.length?pages.filter(name=>requested.includes(name)):pages;
if(requested.length&&requested.length!==targets.length)throw Error('Unknown or repeated page in requested subset');
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const anchors={companions:'.companion-card',orders:'.order-card','companion-picker':'.companion-card',lottery:'.wheel-section','reward-claim':'.claim-heading',messages:'.notice',announcements:'.announcement-card','announcement-detail':'.announcement-article',chat:'.composer',mine:'.identity-card'};
(async()=>{
 const m=await automator.connect({wsEndpoint:'ws://127.0.0.1:19435'});let original;
 try{
  const info=await m.systemInfo();assert.equal(info.model,'iPhone 12/13 (Pro)');assert.equal(info.windowWidth,390);assert.equal(info.screenHeight,844);assert.equal(info.pixelRatio,3);
  original=await m.evaluate(port=>{const c=require('config.js').develop;const x={...c};c.apiBase='http://127.0.0.1:'+port+'/api';c.socketBase='ws://127.0.0.1:9';const a=require('core/auth.js'),app=getApp();app.__xaVisualAuth={getSession:a.getSession,ensure:a.ensure};a.getSession=()=>({token:'isolated-visual-only',user:{id:'visual',role:'customer'}});a.ensure=()=>true;return x},process.env.XA_VISUAL_ASSETS_PORT==='16247'?'16247':'9');
  fs.mkdirSync(output,{recursive:true});
  const defaults=JSON.parse(fs.readFileSync(path.join(fixtureDir,'_defaults.json'),'utf8')),report=[];
  for(const name of targets){
   const entry={name};try{
    const p=await m.reLaunch('/pages/'+name+'/index');await sleep(700);
    const fixture=path.join(fixtureDir,name+'.json');
    const data=fs.existsSync(fixture)?JSON.parse(fs.readFileSync(fixture,'utf8')):extraFixtures[name];
    if(name==='wallet'&&data){data.filter='';data.filters=[{value:'',text:'全部'},{value:'TOPUP',text:'充值'},{value:'PAY',text:'支付'},{value:'REFUND',text:'退款'},{value:'INCOME',text:'收入'}]}
    if(data){await p.setData({...defaults,...data});entry.fixture=true}else entry.fixture=false;
    await sleep(150);const visible=await m.currentPage();assert.equal(visible.path,'pages/'+name+'/index','unexpected redirect to '+visible.path);
    if(anchors[name])assert.ok(await p.$(anchors[name]),'expected business view '+anchors[name]+' is missing');
    if(name==='lottery'&&data){await p.callMethod('paint');await sleep(150)}
    if(bottom){await m.pageScrollTo(9999);await sleep(150)}
    await m.screenshot({path:path.join(output,name+(bottom?'-bottom':'')+'.png')});
    const controls=await p.$$('button');entry.buttons=controls.length;entry.small=[];
    for(const el of controls){try{const d=await el.size();const h=parseFloat(d.height),w=parseFloat(d.width);if(h>0&&w>0&&(h<43||w<43))entry.small.push({height:h,width:w,text:(await el.text()).slice(0,35)})}catch{}}
    entry.ok=true;console.log('PAGE',name,entry.fixture?'fixture':'native-state','buttons',entry.buttons,'small',entry.small.length);
   }catch(e){entry.ok=false;entry.error=e.message;console.log('ERROR',name,e.message)}
   report.push(entry);
  }
  const reportPath=path.join(output,bottom?'report-bottom.json':'report.json');
  const previous=requested.length&&fs.existsSync(reportPath)?JSON.parse(fs.readFileSync(reportPath,'utf8')).pages:[];
  const combined=[...previous.filter(x=>!targets.includes(x.name)),...report];
  fs.writeFileSync(reportPath,JSON.stringify({device:info.model,width:info.windowWidth,screenHeight:info.screenHeight,scale:info.pixelRatio,sampleData:true,pages:combined},null,2));
  console.log('DONE',report.filter(x=>x.ok).length,'/',targets.length);
 }finally{if(original){await m.evaluate(x=>{Object.assign(require('config.js').develop,x);const a=require('core/auth.js'),app=getApp();if(app.__xaVisualAuth){Object.assign(a,app.__xaVisualAuth);delete app.__xaVisualAuth}},original).catch(()=>{});await m.reLaunch('/pages/home/index').catch(()=>{})}m.disconnect()}
})().catch(e=>{console.error(e);process.exitCode=1});
