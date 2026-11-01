import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const prior=path.resolve(root,'../club-sand-safe-insurance-campaign-handdrawn');
const read=async f=>JSON.parse(await fs.readFile(path.join(prior,f),'utf8'));
for(const dir of ['assets/backgrounds','02-layouts','references','.cache','review'])await fs.mkdir(path.join(root,dir),{recursive:true});
for(const f of ['assets/backgrounds/poster.png','assets/backgrounds/social.png','02-layouts/generated-background-measurements.json','source/render.mjs','source/outline-text.ps1','.cache/all-text-outlines.json'])await fs.copyFile(path.join(prior,f),path.join(root,f));
const refs=[
 ['C:/Users/chenh/AppData/Local/Temp/codex-clipboard-afc297cd-1eb1-4ff5-b68e-e1741ee4c148.png','new-mobile-prices.png'],
 ['C:/Users/chenh/AppData/Local/Temp/codex-clipboard-877049e8-ded4-411a-b9ca-6f6d2c06ae4c.png','user-original-advert.png'],
 ['C:/Users/chenh/AppData/Local/Temp/codex-clipboard-ce7d151a-3b55-472f-8cfb-f373a90e28ed.png','user-original-mobile.png']
];
for(const [src,name] of refs)await fs.copyFile(src,path.join(root,'references',name));
const c=await read('content.json'),state=await read('campaign-state.json');
const mobile=c.tiers.find(t=>t.id==='mobile-absolute');
const revised=[88,238,428],before=mobile.rows.map(r=>({...r}));
for(const [i,row]of mobile.rows.entries())row.priceR=revised[i];
c.revision='sand-safe-insurance-mobile-price-r2-20261009';
c.sources.mobilePriceUpdate={date:'2026-10-09',file:'references/new-mobile-prices.png',basis:'用户要求按截图新价格更新；并明确回复本次只改手游绝密和广告页。新价格列88/238/428元，保底688/1688/3288万，沙色次数2/6/10，大保险3/8/15。'};
c.verification.mobilePriceUpdate={status:'verified-against-user-full-resolution-image',before,after:mobile.rows,scope:['mobile-absolute','social'],desktopBasis:'沿用当前已确认项目content.json；只修改手游价格。用户附旧广告端游第二档大保险为3，当前源稿、当前成图及完整端游表均为8，本轮保留当前项目的8。'};
c.delivery={pageIds:['mobile-absolute','social'],platforms:['desktop','mobile'],basis:'手游绝密完整价目及双平台广告两张，广告中的端游价目引用现有已核对数据。'};
state.revision=c.revision;state.stage='content-verified';state.contentVerified=true;
state.contentVerificationBasis=c.sources.mobilePriceUpdate.basis+' 规则、奖励与端游数据沿用原项目已确认内容。';
state.deliveryScope=c.delivery;
state.verifiedPricePlatforms=['desktop','mobile'];
state.priceSourceByPlatform={desktop:'../club-sand-safe-insurance-campaign-handdrawn/content.json；原项目已核对端游档位，广告继续沿用。',mobile:'references/new-mobile-prices.png 新价格列；2026-10-09用户确认只改手游绝密和广告页。'};
state.plannedDeliverables=['手游绝密完整价目表','双平台广告页','高清PNG、可改文字SVG、转曲SVG、成品包和查看页'];
state.approvals.revisionScope={status:'approved',basis:'2026-10-09用户明确回答：本次只改手游绝密和广告页。'};
state.nextStep='矢量更新两张页面并检查实际PNG、390px预览及改价局部。';
delete state.finalDeliverables;delete state.visualQA;delete state.lastRenderedPages;
await fs.writeFile(path.join(root,'content.json'),JSON.stringify(c,null,2)+'\n');
await fs.writeFile(path.join(root,'campaign-state.json'),JSON.stringify(state,null,2)+'\n');
let adapter=await fs.readFile(path.join(prior,'layout-adapter.mjs'),'utf8');
adapter=adapter.replace("pages:[...c.tiers.map(poster),cover(),social()]","pages:[poster(c.tiers.find(t=>t.id==='mobile-absolute')),social()]");
adapter=adapter.replace("version:'sand-safe-insurance-vectors-v1'","version:c.revision");
adapter=adapter.replace("elements:[]});","elements:[],platforms:id==='social'?['desktop','mobile']:['mobile']});");
await fs.writeFile(path.join(root,'layout-adapter.mjs'),adapter);
await fs.writeFile(path.join(root,'README.md'),'# 兴安电竞 · 常规沙保单改价\n\n本轮只更新手游绝密完整价目和双平台广告。手游新价88/238/428元；保底688/1688/3288万；沙色保险2/6/10，常规大保险3/8/15。端游价格及规则、奖励沿用原项目已核对内容。插画与无字底图复用原确认版。\n\ncontent.json为唯一业务源；source/typeset.mjs重建两张成图；review/index.html为查看与下载页。新项目保留旧成品，不覆盖原项目。\n');
console.log(JSON.stringify({root,pageIds:c.delivery.pageIds,mobile:mobile.rows},null,2));
