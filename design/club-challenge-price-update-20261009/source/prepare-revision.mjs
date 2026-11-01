import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const project=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const prior=path.resolve(project,'../club-challenge-campaign-handdrawn');
const skill=path.join(process.env.USERPROFILE,'.codex/skills/xingan-gameplay-posters');
const readJSON=async p=>JSON.parse((await fs.readFile(p,'utf8')).replace(/^\uFEFF/,''));
if(await fs.access(path.join(project,'content.json')).then(()=>true,()=>false))throw Error('Revision already prepared.');
for(const dir of ['assets/backgrounds','source-references','.cache','review'])await fs.mkdir(path.join(project,dir),{recursive:true});
for(const name of ['poster-gold.png','poster-teal.png','social.png'])await fs.copyFile(path.join(prior,'assets/backgrounds',name),path.join(project,'assets/backgrounds',name));
await fs.copyFile(path.join(prior,'layout-adapter.mjs'),path.join(project,'layout-adapter.mjs'));
await fs.copyFile(path.join(prior,'.cache/font-outlines.json'),path.join(project,'.cache/font-outlines.json'));
for(const name of ['workflow.mjs','render.mjs','scope.mjs','outline-text.ps1','pack.ps1'])await fs.copyFile(path.join(skill,'scripts',name),path.join(project,'source',name));
const attached=[
 ['codex-clipboard-dec282e5-6b22-4de8-8d3d-69f63e8da5e3.png','new-mobile-prices.png'],
 ['codex-clipboard-aef5b832-ca21-4029-afa2-ec778a0ca3db.png','prior-social.png'],
 ['codex-clipboard-210dd42e-b238-4974-9742-d2aa7f3e93e6.png','prior-mobile-absolute.png'],
 ['codex-clipboard-a86149e9-5a04-4159-9c14-2de2d293ce0a.png','prior-mobile-secret.png'],
];
for(const [from,to]of attached)await fs.copyFile(path.join(process.env.LOCALAPPDATA,'Temp',from),path.join(project,'source-references',to));
const content=await readJSON(path.join(prior,'content.json'));
const prices={
 'mobile-absolute':{'航天':[248,428,728],'巴克什':[228,388,588],'监狱':[228,388,588]},
 'mobile-secret':{'核电站':[168,248,388],'巴克什':[188,298,428],'航天':[198,328,468]},
};
const changes=[];
for(const t of content.tiers.filter(t=>t.platform==='手游'))for(const m of t.maps)m.rows.forEach((r,i)=>{
 const newPriceR=prices[t.id][m.name][i];
 changes.push({pageId:t.id,mode:t.mode,map:m.name,tier:i+1,oldPriceR:r.priceR,newPriceR,thresholdW:r.thresholdW,comparison:r.comparison});
 r.priceR=newPriceR;
});
content.deliveryPageIds=['mobile-absolute','mobile-secret','social'];
const sources={desktop:'本轮附件 prior-social.png 上方端游绝密、机密两卡，18档与此前已确认源数据逐项一致；广告继续沿用。',mobile:'本轮附件 new-mobile-prices.png：右侧“新价格”列与保底/模式/地图逐行对应。绝密巴克什/监狱三行分别展开为两地图，合计手游18档。'};
content.provenance={...content.provenance,verified:true,prices:'2026-10-09：手游18档价格以本轮新价格截图为准；保底目标逐项核对截图并与原稿一致。端游18档按本轮广告图沿用。',rules:'本轮三张海报附件及既有确认规则沿用：严格大于目标，等于未达标，不跨局累计；补偿与须知沿用。',sourceFiles:attached.map(([,to])=>'source-references/'+to),priceSourceByPlatform:sources,priceRevision:{date:'2026-10-09',changes,advertisingLayout:'手游各地图价格不同，广告下方两卡改为三地图列，每格上行价格、下行严格大于目标。'}};
const oldState=await readJSON(path.join(prior,'campaign-state.json'));
const approvals=Object.fromEntries(Object.entries(oldState.approvals).map(([key,a])=>[key,{status:a.status,basis:a.basis}]));
const state={skill:'xingan-gameplay-posters',campaign:oldState.campaign,date:'2026-10-09',priorProject:prior,stage:'content-verified',revision:'challenge-mobile-price-r2-20261009',contentVerified:true,contentVerificationBasis:'本轮15行价格截图映射手游18档，保留严格大于及原目标值。原广告两张端游卡18档与源数据一致。用户明确要求更新物资对赌单广告页和两张手游价目，既有视觉与规则沿用。',deliveryScope:{pageIds:content.deliveryPageIds,platforms:['desktop','mobile'],basis:'用户点名广告页、手游绝密和机密价格表，共3页；广告上方保留本轮附图中的端游价目。'},verifiedPricePlatforms:['desktop','mobile'],priceSourceByPlatform:sources,approvals,plannedDeliverables:['手游·绝密三地图各三档价目','手游·机密三地图各三档价目','物资对赌单总广告页'],layoutInvariants:oldState.layoutInvariants,priceChanges:changes,vectorDelivery:{status:'in-progress',formats:['PNG','editable text SVG','outlined SVG'],pageIds:content.deliveryPageIds,changedPriceCount:18},nextStep:'只排版并导出三张受影响页面，按最终PNG、手机预览与价目局部复检后交付。'};
await fs.writeFile(path.join(project,'content.json'),JSON.stringify(content,null,2));
await fs.writeFile(path.join(project,'campaign-state.json'),JSON.stringify(state,null,2));
console.log(JSON.stringify({project,pages:content.deliveryPageIds,changedPriceCount:changes.length,changes}));
