import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const project=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const prior=path.resolve(project,'../club-jiahao-campaign-handdrawn');
const skill='C:/Users/chenh/.codex/skills/xingan-gameplay-posters';
const read=async p=>JSON.parse((await fs.readFile(p,'utf8')).replace(/^\uFEFF/,''));
const write=async (p,v)=>fs.writeFile(p,JSON.stringify(v,null,2)+'\n');
for(const dir of ['assets/backgrounds','references','.cache','review','source'])await fs.mkdir(path.join(project,dir),{recursive:true});
for(const file of ['poster-gold.png','poster-teal.png','social.png'])await fs.copyFile(path.join(prior,'assets/backgrounds',file),path.join(project,'assets/backgrounds',file));
for(const file of ['workflow.mjs','render.mjs','scope.mjs','outline-text.ps1','pack.ps1'])await fs.copyFile(path.join(skill,'scripts',file),path.join(project,'source',file));
await fs.copyFile(path.join(prior,'.cache/font-outlines.json'),path.join(project,'.cache/font-outlines.json'));
const refs=[
 ['new-mobile-prices.png','C:/Users/chenh/AppData/Local/Temp/codex-clipboard-d6e8f796-a33b-477f-b6c7-536df69766b8.png'],
 ['prior-social.png','C:/Users/chenh/AppData/Local/Temp/codex-clipboard-53f6e8d2-e283-479b-89a9-c24b34fd2b0a.png'],
 ['prior-mobile-absolute.png','C:/Users/chenh/AppData/Local/Temp/codex-clipboard-7dfe41f8-e592-4f6e-9327-28896ea77e47.png'],
 ['prior-mobile-secret.png','C:/Users/chenh/AppData/Local/Temp/codex-clipboard-9303952e-b648-40aa-8bae-47fb351bda65.png']
];
for(const [name,file] of refs)await fs.copyFile(file,path.join(project,'references',name));
const content=await read(path.join(prior,'content.json'));
const sourceRows={
 'mobile-absolute':[[168,888],[268,1688],[368,2288],[468,3088]],
 'mobile-secret':[[128,888],[228,1688],[328,2288],[428,3088]]
};
const comparison=[];
for(const [id,rows] of Object.entries(sourceRows)){
 const tier=content.tiers.find(t=>t.id===id);
 comparison.push({id,previous:tier.rows.map(r=>[r.priceR,r.lootW]),current:rows,numericalChange:JSON.stringify(tier.rows.map(r=>[r.priceR,r.lootW]))!==JSON.stringify(rows)});
 tier.rows=rows.map(([priceR,lootW])=>({priceR,lootW}));
}
content.provenance={...content.provenance,prices:'2026-10-09：手游以本轮表格 J 列新价格与 E 列保底逐行对应 F 列模式；端游按本轮广告截图两张上方卡片沿用。',verified:true,verifiedDate:'2026-10-09',sourceFiles:refs.map(([name])=>'references/'+name),priceSourceByPlatform:{desktop:'references/prior-social.png，上方端游两卡；逐行与原 content.json 一致。',mobile:'references/new-mobile-prices.png，J列新价格与E列保底、F列模式；共8档，地图航天。'},numericalComparison:comparison};
await write(path.join(project,'content.json'),content);
const priorState=await read(path.join(prior,'campaign-state.json'));
const state={skill:'xingan-gameplay-posters',campaign:priorState.campaign,date:'2026-10-09',priorProject:prior,stage:'content-verified',revision:'jiahao-price-r2',contentVerified:true,contentVerificationBasis:'本轮手游表格8档与原源稿及用户附图逐行一致；重新记录本轮表格为最新依据。端游两卡与所附广告图一致；保障、清图方式与须知按附图和既有确认沿用。',deliveryScope:{pageIds:['mobile-absolute','mobile-secret','social'],platforms:['desktop','mobile'],basis:'用户点名嘉豪物资单广告页与手游绝密、机密两张价格表；广告保留端游两卡，共三张。'},verifiedPricePlatforms:['desktop','mobile'],priceSourceByPlatform:content.provenance.priceSourceByPlatform,approvals:priorState.approvals,layoutInvariants:priorState.layoutInvariants,priceComparison:comparison,plannedDeliverables:['手游·绝密四档价目','手游·机密四档价目','嘉豪物资单广告页'],nextStep:'导出三张并复检，复用已确认插画与底图。'};
await write(path.join(project,'campaign-state.json'),state);
let adapter=await fs.readFile(path.join(prior,'layout-adapter.mjs'),'utf8');
adapter=adapter.replace("return {version:'jiahao-v1',requests,pages:[...content.tiers.map(poster),cover(),social()]};", "const pages=[...content.tiers.filter(t=>t.platform==='手游').map(poster),social()];\nfor(const p of pages)p.platforms=p.id==='social'?['desktop','mobile']:['mobile'];\nreturn {version:'jiahao-price-20261009',requests,pages};");
if(!adapter.includes("version:'jiahao-price-20261009'"))throw Error('Adapter return signature not found.');
await fs.writeFile(path.join(project,'layout-adapter.mjs'),adapter);
await write(path.join(project,'review/source-comparison.json'),comparison);
console.log(JSON.stringify({project,pages:state.deliveryScope.pageIds,comparison},null,2));
