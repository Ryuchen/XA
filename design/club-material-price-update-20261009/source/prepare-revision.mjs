import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const project=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const prior=path.resolve(project,'../club-material-campaign-v3-handdrawn');
const read=async p=>JSON.parse((await fs.readFile(p,'utf8')).replace(/^\uFEFF/,''));
const write=async(p,v)=>fs.writeFile(p,JSON.stringify(v,null,2));
const content=await read(path.join(prior,'06-typesetting/content.json'));
const updates={
  'mobile-absolute':{prices:[148,238,468,548,688,808],loot:[1388,2288,4688,5688,7688,9688]},
  'mobile-secret':{prices:[138,248,388,528,628,868],loot:[1588,2888,4688,6688,8688,12688]}
};
for(const t of content.tiers)if(updates[t.id])Object.assign(t,updates[t.id]);
content.source='2026-10-09：手游采用用户附件表格 J 列新价格与 E 列保底按 F 列模式逐行对应；H 列为旧档位标签，不作为现售价。端游价目、活动、保障与须知按本轮附图沿用。';
for(const mode of ['绝密','机密'])content.rules[mode][2].compensation=mode==='绝密'?[[200,300],[300,200]]:[[100,150],[150,100]];
await write(path.join(project,'content.json'),content);
let code=await fs.readFile(path.join(prior,'06-typesetting/build-final.mjs'),'utf8');
code=code.slice(code.indexOf("const ink="),code.indexOf("await fs.writeFile(path.join(here,'text-requests.json')"));
code=code.replace("const values=t.mode==='绝密'?[[200,300],[300,200]]:[[100,150],[150,100]];","const values=r.compensation;");
code=code.replace('const pages=[...content.tiers.map(poster),cover(),social()];',"const pages=[...content.tiers.filter(t=>t.platform==='手游').map(poster),social()];\nfor(const p of pages)p.platforms=p.id==='social'?['desktop','mobile']:['mobile'];\nreturn {requests,pages};");
await fs.writeFile(path.join(project,'layout-adapter.mjs'),'// Reuses the approved r2 vector composition. Only three requested pages are exported.\nexport function makeLayout(content){\n'+code+'\n}\n');
for(const name of ['poster-gold.png','poster-teal.png','social.png'])await fs.copyFile(path.join(prior,'03-backgrounds',name),path.join(project,'assets/backgrounds',name));
await fs.mkdir(path.join(project,'references'),{recursive:true});
const attachments=[['9b0ace7d-8d33-443c-aef5-4eb79bbb24c2','new-mobile-prices'],['1ca04ab9-b92b-4c86-9f1c-cb7639e7e4a5','prior-social'],['2be9d941-da1b-44a2-b2be-14c1dca1c3d5','prior-mobile-absolute'],['87e83f40-be9e-4a8d-964a-f1ac2cdf057b','prior-mobile-secret']];
for(const [id,name] of attachments)await fs.copyFile(path.join(process.env.TEMP,`codex-clipboard-${id}.png`),path.join(project,'references',name+'.png'));
const state=await read(path.join(project,'campaign-state.json'));
Object.assign(state,{date:'2026-10-09',revision:'r3-price-update',stage:'content-verified',contentVerified:true,contentVerificationBasis:content.source,deliveryScope:{pageIds:['mobile-absolute','mobile-secret','social'],platforms:['desktop','mobile'],basis:'用户点名更新常规物资单广告页及两张手游价格表；广告含两端四类价目，端游按附图沿用。'},verifiedPricePlatforms:['desktop','mobile'],priceSourceByPlatform:{desktop:'本轮附件 prior-social.png 上方两卡与历史 content.json 逐行一致，按用户仅改手游的范围沿用。',mobile:'references/new-mobile-prices.png：J列新价格与E列保底、F列模式逐行对应，共12档。'},priorProject:prior,approvals:{illustration:{status:'approved-reuse',basis:'用户提供的现有三张成图'},background:{status:'approved-reuse',basis:'只改价目，复用原项目相同底图'}}});
await write(path.join(project,'campaign-state.json'),state);
console.log('Prepared 3-page revision with 12 verified mobile price/guarantee pairs.');
