import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
const [project,modules]=process.argv.slice(2);
const coverOnly=process.argv.includes('--cover-only');
const sharp=createRequire(path.join(modules,'..','sand-safe-review-loader.cjs'))('sharp');
const read=async f=>JSON.parse(await fs.readFile(path.join(project,f),'utf8'));
const c=await read('content.json'),m=await read('02-layouts/final-background-measurements.json'),plan=await read('layout.json');
const expected={desktop:[218,428,628,588,988],mobile:[168,328,488,488,888]};
const ids=['desktop-absolute','mobile-absolute','combined-advert','combined-cover'];
if(JSON.stringify(plan.pages.map(p=>p.id))!==JSON.stringify(ids))throw Error('Exactly two price pages, one merged advert and one common cover required.');
if(c.prices.length!==5||['desktop','mobile'].some(k=>c.prices.some((r,i)=>r[k]!==expected[k][i])))throw Error('Source prices differ from user text.');
if(c.objectives.loot.operator!=='>'||c.objectives.loot.desktop!==888||c.objectives.loot.mobile!==688||c.wishReward.guarantee!==1688||c.objectives.sandSafes.count!==2||c.objectives.regularSafes.count!==4)throw Error('Source conditions mismatch.');
const broken=c.notices.find(v=>v.id==='broken-order');
if(broken.operator!=='<'||broken.loot!==300||!broken.interruptConsecutive||broken.increaseGuarantee)throw Error('Broken-order rule mismatch.');
const priceCrops=[['header',{x:0,y:35,width:836,height:270}],['reward',{x:34,y:310,width:767,height:170}],['price',{x:36,y:491,width:765,height:374}],['objectives',{x:36,y:878,width:764,height:371}],['notices',{x:36,y:1262,width:764,height:408}],['decor',{x:0,y:1690,width:836,height:191}]];
const coverCrops=m.cover.reviewCrops;
const advertCrops=[['header',{x:36,y:65,width:1040,height:339}],['price',{x:32,y:411,width:1021,height:412}],['targets',{x:32,y:836,width:1021,height:239}],['reward',{x:40,y:1088,width:1013,height:306}]];
const checks=[];
for(const page of plan.pages){
  const isPrice=page.pageKind==='price',isCover=page.pageKind==='cover',prefix=isPrice?page.sourcePlatform:page.id,file=path.join(project,'output/png',page.id+'.png');
  const bounds=await read('review/'+page.id+'-text-bounds.json'),meta=await sharp(file).metadata(),wanted=isPrice?[3200,7200]:isCover?[3200,1800]:[2400,3200];
  if(meta.width!==wanted[0]||meta.height!==wanted[1])throw Error('Wrong export size '+page.id);
  if(!coverOnly||isCover){
  await sharp(file).resize({width:390}).png().toFile(path.join(project,'review',prefix+'-phone.png'));
  await sharp(file).resize({width:1200}).png().toFile(path.join(project,'review',prefix+'-final-preview.png'));
  for(const [name,b] of isPrice?priceCrops:isCover?coverCrops:advertCrops){
    const k=meta.width/page.w,left=Math.round(b.x*k),top=Math.round(b.y*k),width=Math.min(Math.round(b.width*k),meta.width-left),height=Math.min(Math.round(b.height*k),meta.height-top);
    await sharp(file).extract({left,top,width,height}).resize({width:1200}).png().toFile(path.join(project,'review',prefix+'-'+name+'.png'));
  }
  }
  for(const b of bounds)if(b.effectiveSize<=0||b.x<0||b.y<0||b.x+b.width>page.w+.05||b.y+b.height>page.h+.05)throw Error('Text outside canvas '+page.id+' '+b.text);
  for(const v of plan.placements.filter(v=>v.page===page.id))if(v.visible.x<v.box.x-.05||v.visible.y<v.box.y-.05||v.visible.x+v.visible.width>v.box.x+v.box.width+.05||v.visible.y+v.visible.height>v.box.y+v.box.height+.05)throw Error('Text group outside safe area '+page.id+' '+v.role);
  const text=page.elements.filter(e=>e.type==='text').map(e=>e.text).join('');
  for(const s of ['万金泪冠 / 纵横','下单时许愿（二选一）',...(isCover?[]:['同一局成功撤离，三项同时达成'])])if(!text.includes(s))throw Error('Missing condition '+s);
  if(!text.includes(isPrice?'如果出了，赠送':isCover?c.cover.wishTriggerShort:'出了就送'))throw Error('Reward trigger omitted.');
  if(!isCover&&JSON.stringify(bounds.filter(b=>b.role==='objective-count').map(b=>Number(b.text)))!=='[2,4]')throw Error('Safe counts mismatch.');
  const keys=isPrice?[page.sourcePlatform]:['desktop','mobile'];
  const numericRoles=[['reward',1688],...(isCover?[]:keys.map(k=>['loot-'+k,c.objectives.loot[k]])),...(isPrice?[['broken-order-loot',300]]:[])];
  for(const [role,value] of numericRoles){
    const ns=bounds.filter(b=>b.role===role+'-number'),us=bounds.filter(b=>b.role===role+'-unit');
    if(ns.length!==1||us.length!==1||Number(ns[0].text)!==value)throw Error('Numeric condition mismatch '+role);
    if(Math.abs(us[0].height/ns[0].height-.72)>.01||Math.abs(us[0].cy-ns[0].cy)>.01||us[0].x<=ns[0].x+ns[0].width)throw Error('Numeric unit spacing/height mismatch '+role);
  }
  const nums=bounds.filter(b=>b.role==='price-number'),units=bounds.filter(b=>b.role==='price-unit'),wantPrices=isCover?[]:keys.flatMap(k=>expected[k]);
  if(JSON.stringify(nums.map(b=>Number(b.text)))!==JSON.stringify(wantPrices)||units.length!==wantPrices.length)throw Error('Price display mismatch '+page.id);
  if(!isCover){
    const cols=page.alignment.priceUnitColumns;
    if(cols.length!==keys.length||cols.some((v,i)=>v.platform!==keys[i]))throw Error('Platform columns mismatch.');
    for(let j=0;j<keys.length;j++)for(let i=0;i<5;i++){
      const n=nums[j*5+i],u=units[j*5+i],cy=page.alignment.priceRowCenters[i];
      if(Math.abs(u.x-cols[j].unitX)>.01||Math.abs(u.x-n.x-n.width-8)>.01||Math.abs(u.height/n.height-.72)>.01||Math.abs(n.cy-cy)>.01||Math.abs(u.cy-cy)>.01)throw Error('Price alignment mismatch '+page.id);
    }
  }
  if(isPrice){
    const label=page.sourcePlatform==='desktop'?'端游':'手游',other=page.sourcePlatform==='desktop'?'手游':'端游';
    if(!text.includes('绝密 · '+label)||text.includes(other))throw Error('Price poster platform mismatch.');
    for(const s of ['三项目标同局完成','低于','中断连续计数，','不增加保底。','询问板板，','绝密地图'])if(!text.includes(s))throw Error('Missing notice '+s);
    const left=page.alignment.noticeLeft;
    if(page.alignment.noticeAlignment!=='left'||Math.abs(bounds.find(b=>b.role==='notice-heading').x-left)>.01)throw Error('Notice heading should share left edge.');
    for(const role of ['rule-completion','rule-broken-order','rule-safe-interaction','rule-extra-guarantee']){
      const rows=new Map();for(const b of bounds.filter(b=>b.role===role))rows.set(b.cy,Math.min(rows.get(b.cy)??Infinity,b.x));
      if(!rows.size||[...rows.values()].some(x=>Math.abs(x-left)>.01))throw Error('Notice paragraph left edges differ.');
    }
    if(Math.min(...bounds.filter(b=>b.role?.startsWith('rule-')).map(b=>b.effectiveSize))<30.9)throw Error('Rule body shrunk.');
    const lootText=page.elements.filter(e=>e.type==='text'&&(e.role==='loot-target'||e.role?.startsWith('loot-'))).map(e=>e.text).join('');
    if(lootText!=='单局物资保底>'+c.objectives.loot[page.sourcePlatform]+'w')throw Error('Single-platform target wording mismatch.');
  }else{
    if(page.sourcePlatform!=='combined'||!text.includes('绝密 · 端游 / 手游')||(!isCover&&(!text.includes('端游 >')||!text.includes('手游 >'))))throw Error('Merged platform mapping missing.');
    if(['下单须知','炸单','300','不增加保底','补打','询问板板'].some(s=>text.includes(s)))throw Error('Notice/settlement exception on cover/advert.');
    for(const v of plan.placements.filter(v=>v.page===page.id))if(Math.abs(v.visible.x+v.visible.width/2-v.center[0])>.01)throw Error('Short-page copy not centered.');
    if(isCover){
      if(!text.includes('累计挑战 · 连续挑战')||bounds.some(b=>b.role==='cover-entry-number'||b.role==='objective-count'||b.role?.startsWith('loot-')))throw Error('Standard cover summary mismatch.');
      if(Math.abs(page.w/page.h-16/9)>.003||page.alignment.format!=='16:9')throw Error('Cover is not standard widescreen.');
      for(const v of plan.placements.filter(v=>v.page===page.id))if(Math.abs(v.visible.x+v.visible.width/2-v.box.x-v.box.width/2)>.01)throw Error('Cover group not centered in its copy area.');
      if(page.alignment.bodySize*390/page.w<14)throw Error('Cover body too small on phone.');
    }
  }
  checks.push({page:page.id,kind:page.pageKind,platforms:keys,prices:wantPrices,exportSize:wanted,nativeBackgroundSize:[page.w,page.h],phoneBodyPx:page.alignment.bodySize*390/page.w,noticeAlignment:isPrice?'left':null,requiredConditionsPreserved:true,mergedPlatforms:!isPrice});
}
const audit={revision:'sand-safe-r9-standard-cover',pages:checks,pricesChecked:20,coverEntryPricesChecked:0,requiredPageCount:4,visualReview:'Inspect completely redrawn standard horizontal cover full PNG, 390px preview and hero/reward crops; unchanged other pages retain r8 actual-image QA.'};
await fs.writeFile(path.join(project,'review/typesetting-audit.json'),JSON.stringify(audit,null,2)+'\n');
console.log(JSON.stringify({pages:4,prices:20,bodyPx:checks.map(p=>p.phoneBodyPx),alignment:'20/20',previews:ids}));
