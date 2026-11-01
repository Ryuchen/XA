import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const project=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const prior=path.resolve(project,'../club-timed-clear-campaign-handdrawn');
const skill=process.argv[2];
const read=async f=>JSON.parse((await fs.readFile(f,'utf8')).replace(/^\uFEFF/,''));
const write=async(n,o)=>fs.writeFile(path.join(project,n),JSON.stringify(o,null,2));
const pageIds=['mobile-absolute','mobile-secret','social'];
const prices={'mobile-absolute':[108,188,328,468,668,888],'mobile-secret':[88,128,188,288,468,688]};
const refs={
 'new-mobile-prices.png':'C:/Users/chenh/AppData/Local/Temp/codex-clipboard-1a3f9e94-394b-487c-8a03-83d60daaf575.png',
 'prior-social.png':'C:/Users/chenh/AppData/Local/Temp/codex-clipboard-961b4c45-f25e-46e7-9d5e-cd19da1ba9aa.png',
 'prior-mobile-absolute.png':'C:/Users/chenh/AppData/Local/Temp/codex-clipboard-1f209878-b289-4d60-bce8-2761ae4dd5d2.png',
 'prior-mobile-secret.png':'C:/Users/chenh/AppData/Local/Temp/codex-clipboard-9734afb3-04e9-4e22-8d99-c7fd75c71432.png'
};
for(const dir of ['assets/backgrounds','source-references','.cache','review'])await fs.mkdir(path.join(project,dir),{recursive:true});
for(const [n,f]of Object.entries(refs))await fs.copyFile(f,path.join(project,'source-references',n));
for(const n of ['poster-gold.png','poster-teal.png','social.png'])await fs.copyFile(path.join(prior,'assets/backgrounds',n),path.join(project,'assets/backgrounds',n));
for(const n of ['workflow.mjs','render.mjs','scope.mjs','outline-text.ps1'])await fs.copyFile(path.join(skill,'scripts',n),path.join(project,'source',n));
await fs.copyFile(path.resolve(project,'../club-challenge-price-update-20261009/source/pack.ps1'),path.join(project,'source/pack.ps1'));
const content=await read(path.join(prior,'content.json'));
const changes=[];
for(const t of content.tiers.filter(t=>prices[t.id]))t.rows.forEach((r,i)=>{
 changes.push({pageId:t.id,mode:t.mode,tier:r.tierType,deadlineMinutes:r.deadlineMinutes,oldPriceR:r.priceR,newPriceR:prices[t.id][i]});
 r.priceR=prices[t.id][i];
 r.materialGuarantee={specified:false,sourceCopy:'未列固定物资保底'};
});
content.deliveryPageIds=pageIds;
content.sources.mobilePrice={date:'2026-10-09',file:'source-references/new-mobile-prices.png',column:'新价格',rowOrder:['常规清图',16,14,12,10,8],guarantee:'未列固定物资保底；不是新的固定物资承诺。清图最低要求及其他规则沿用用户本轮附图。'};
content.sources.desktopPrice='本轮prior-social.png上方端游两卡与已确认源数据逐行一致，继续沿用。';
content.sources.price='手游采用2026-10-09新价格列；端游沿用本轮广告参考图。';
content.sources.referenceFiles=Object.keys(refs).map(n=>'source-references/'+n);
content.contentNotes.push('2026-10-09仅更新手游限时档价格：绝密188/328/468/668/888，机密128/188/288/468/688；常规档108/88不变。','本轮未提供新的固定物资保底数额；附图清图最低要求、房卡补偿、速撤与清图条件沿用。');
await write('content.json',content);
const priorState=await read(path.join(prior,'campaign-state.json'));
await write('campaign-state.json',{
 skill:'xingan-gameplay-posters',campaign:'兴安电竞 · 限时清图单',date:'2026-10-09',priorProject:prior,
 stage:'content-verified',revision:'timed-clear-mobile-price-r3-20261009',contentVerified:true,
 contentVerificationBasis:'本轮12行价目截图右侧新价格列；手游模式、航天地图及时限逐行对应。表中未列固定物资保底，无新金额；原图最低要求和规则继续沿用。广告端游两卡12档与源数据一致。',
 deliveryScope:{pageIds,platforms:['desktop','mobile'],basis:'用户点名限时清图单广告页及手游绝密/机密价格表，共3页；广告上方端游价目继续沿用本轮参考。'},
 verifiedPricePlatforms:['desktop','mobile'],priceSourceByPlatform:{desktop:'source-references/prior-social.png上方端游两卡12档，逐项与旧已确认源数据一致。',mobile:'source-references/new-mobile-prices.png右侧新价格列12行，按常规及16/14/12/10/8分钟逐行核对。'},
 approvals:priorState.approvals,plannedDeliverables:['手游绝密六档价格页','手游机密六档价格页','限时清图单广告页'],priceChanges:changes,
 nextStep:'复用已确认底图，只导出3张受影响页面；执行业务与最终PNG视觉复检后打包。'
});
let adapter=await fs.readFile(path.join(prior,'layout-adapter.mjs'),'utf8');
adapter=adapter.replace("return {id,label,bg,w,h,ew,eh,elements:[]};","return {id,label,bg,w,h,ew,eh,platforms:id==='social'?['desktop','mobile']:[id.startsWith('mobile')?'mobile':'desktop'],elements:[]};");
const oldInline="function inline(p,parts,x,cy,{anchor='start',maxWidth,gap=8,role='inline-copy'}={}){p.elements.push({type:'inline',x,cy,anchor,maxWidth,gap,role,parts:parts.map(s=>({text:String(s.text),id:request(s.text,s.font??CN,s.style??1),size:s.size??20,fill:s.fill??ink,gap:s.gap,role:s.role??role}))});}";
const newInline="function inline(p,parts,x,cy,{anchor='start',maxWidth,gap=8,role='inline-copy'}={}){p.elements.push({type:'rich',x,cy,anchor,maxWidth,size:20,role,group:`${role}:${x}:${cy}`,runs:parts.map(s=>({text:String(s.text),id:request(s.text,s.font??CN,s.style??1),size:s.size??20,fill:s.fill??ink,gap:s.gap??gap,role:s.role??role}))});}";
if(!adapter.includes(oldInline))throw Error('Prior inline helper changed');
adapter=adapter.replace(oldInline,newInline);
adapter=adapter.replace("tx(p,unit,unitX,cy,unitSize,{font:unitFont,fill,role:role+'-unit'});}","tx(p,unit,unitX,cy,unitSize,{font:unitFont,fill,role:role+'-unit'}); for(const e of p.elements.slice(-2))e.lineGroup=`${role}:${unitX}:${cy}`;}");
adapter=adapter.replace("return {version:'timed-clear-v1',requests,pages:[...content.tiers.map(poster),cover(),social()]};","return {version:'timed-clear-mobile-price-r3',requests,pages:[...content.tiers.filter(t=>content.deliveryPageIds.includes(t.id)).map(poster),social()]};");
await fs.writeFile(path.join(project,'layout-adapter.mjs'),adapter);
// Retain semantic run roles for the revision's baseline and spacing audit.
const rendererPath=path.join(project,'source/render.mjs');
let renderer=await fs.readFile(rendererPath,'utf8');
renderer=renderer.replace("role:r.bold?'key-condition':e.role","role:r.role??(r.bold?'key-condition':e.role)");
await fs.writeFile(rendererPath,renderer);
console.log(JSON.stringify({project,pageIds,newPrices:prices,changedPrices:changes.filter(c=>c.oldPriceR!==c.newPriceR).length}));
