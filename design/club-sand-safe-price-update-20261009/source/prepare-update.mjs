import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const project=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const base=path.resolve(project,'../club-sand-safe-redesign-20261003');
const read=async file=>JSON.parse((await fs.readFile(file,'utf8')).replace(/^\uFEFF/,''));
const write=async(file,value)=>fs.writeFile(file,JSON.stringify(value,null,2));
const revision='price-r2-20261009';
for(const dir of ['assets/backgrounds','02-layouts','.cache','references','source/before-update','review','output'])await fs.mkdir(path.join(project,dir),{recursive:true});
for(const name of ['content.json','layout.json','campaign-state.json'])await fs.copyFile(path.join(base,name),path.join(project,'source/before-update',name));
await fs.copyFile(path.join(base,'layout-adapter.mjs'),path.join(project,'layout-adapter.mjs'));
await fs.copyFile(path.join(base,'02-layouts/generated-background-measurements.json'),path.join(project,'02-layouts/generated-background-measurements.json'));
await fs.copyFile(path.join(base,'.cache/font-outlines.json'),path.join(project,'.cache/font-outlines.json'));
for(const name of ['workflow.mjs','render.mjs','scope.mjs','outline-text.ps1','prepare-final-review.mjs','package-final.mjs','pack-final.ps1'])await fs.copyFile(path.join(base,'source',name),path.join(project,'source',name));
const pageIds=['desktop-absolute','mobile-absolute','combined-advert','combined-cover'];
for(const id of pageIds)await fs.copyFile(path.join(base,'assets/backgrounds',id+'.png'),path.join(project,'assets/backgrounds',id+'.png'));
const attachments=[
 ['new-mobile-prices.png','codex-clipboard-88fa91ee-495c-458d-af36-99fa75409480.png'],
 ['existing-cover.png','codex-clipboard-48c284f2-021a-497a-b4f5-618bf23a573a.png'],
 ['existing-advert.png','codex-clipboard-367d83b1-c48f-4c48-a6c8-2774b6123498.png'],
 ['existing-desktop.png','codex-clipboard-2534353d-8e2f-4198-82ec-57b162d3d74e.png'],
 ['existing-mobile.png','codex-clipboard-5e7dc61a-3c11-447d-be2d-b3e8101ab194.png']
];
for(const [name,file]of attachments)await fs.copyFile(path.join(process.env.LOCALAPPDATA,'Temp',file),path.join(project,'references',name));
const c=await read(path.join(base,'content.json'));
const prices=[108,188,268,388,688];
c.prices.forEach((tier,i)=>tier.mobile=prices[i]);
c.objectives.loot.desktop=788;
c.objectives.loot.mobile=688;
c.objectives.loot.original='2026-10-09用户指定：端游物资保底改成788，手游改成688；沿用既有达标逻辑，单局带出物资严格>端游788w/手游688w。';
c.source={type:'user-message-and-attachments',date:'2026-10-09',basis:'本轮价格截图的新价格列与用户直接指定的物资门槛；端游价格及其他规则沿用附件和已确认源稿。',priorVerifiedSource:'../club-sand-safe-redesign-20261003/content.json',priceImage:'references/new-mobile-prices.png',existingPosterImages:attachments.slice(1).map(x=>'references/'+x[0]),scopeClarification:'用户明确回复：本次只更新绝密四张套图，“机密”为笔误。',mobilePriceMapping:c.prices.map(t=>({id:t.id,label:t.label,newPrice:t.mobile,unit:t.unit})),lootThresholdBasis:'用户直接指定端游788、手游688；保留原严格大于符号和单位w。',rewardBasis:'许愿奖励1688w不是本轮物资达标门槛，沿用附件中明确的奖励。'};
c.verification.status='checked-against-20261009-attachments-and-explicit-scope-reply';
c.verification.preserve=c.verification.preserve.map(x=>x.startsWith('严格超过')?'严格超过端游788w/手游688w':x);
c.delivery.basis='2026-10-09用户要求更新现有四张套图，并确认只更新绝密。';
c.delivery.wordingBasis='本轮手游价格108/188/268/388/688r，端游物资门槛>788w，手游>688w；保留三项同局全部满足且成功撤离及既有规则。';
c.visual.basis='复用用户附件对应的2026-10-03四张已确认插画和底图，仅修改矢量文案。';
c.revision=revision;
await write(path.join(project,'content.json'),c);
const original=await read(path.join(base,'campaign-state.json'));
const state={skill:'xingan-gameplay-posters',revision,stage:'content-verified',contentVerified:true,contentVerificationBasis:'已查看本轮高清价格截图：累计1/2/3次108/188/268，连续2/3次388/688。用户指定端游物资788、手游688，保留>；回复确认“机密”为笔误，本次只改绝密四张。端游218/428/628/688/1288r及许愿奖励1688w由本轮附件和旧源稿交叉核对。',deliveryScope:{pageIds,platforms:['desktop','mobile'],basis:'用户确认本次只更新绝密四张套图：端游价目、手游价目、合并广告、横版封面。'},verifiedPricePlatforms:['desktop','mobile'],priceSourceByPlatform:{desktop:'用户本轮端游附件及合并广告：218/428/628/688/1288r；价格沿用，物资达标门槛按本轮直接指令改为>788w。',mobile:'references/new-mobile-prices.png 新价格列108/188/268/388/688r；本轮直接指令物资达标门槛>688w。'},approvals:original.approvals,reusedVisuals:{sourceProject:'../club-sand-safe-redesign-20261003',basis:'本轮附件与该已确认四张套图一致；全部复用，无新插画或底图。'},clarifications:[{date:'2026-10-09',question:'手游机密是否也要更新',answer:'本次只更新绝密四张套图，“机密”为笔误'}],finalQA:{status:'pending',revision},nextStep:'矢量更新、渲染四张受影响页面、实际PNG和手机预览复检。'};
await write(path.join(project,'campaign-state.json'),state);
let review=await fs.readFile(path.join(project,'source/prepare-final-review.mjs'),'utf8');
review=review.replace("['888','788'].includes(x.number)","[String(content.objectives.loot.desktop),String(content.objectives.loot.mobile)].includes(x.number)");
await fs.writeFile(path.join(project,'source/prepare-final-review.mjs'),review);
console.log(JSON.stringify({project,revision,pages:pageIds,mobilePrices:prices,loot:c.objectives.loot,reusedBackgrounds:4}));
