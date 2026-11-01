import fs from 'node:fs';
import path from 'node:path';
const root=path.resolve(import.meta.dirname,'..');
const read=f=>JSON.parse(fs.readFileSync(f,'utf8').replace(/^\uFEFF/,''));
const write=(f,c)=>fs.writeFileSync(path.join(root,f),JSON.stringify(c,null,2)+'\n');
const c=read(path.join(root,'content.json')),previous=read(process.argv[2]);
if(previous.title!==c.title||previous.brand!==c.brand)throw Error('Source project mismatch');
const mobile=previous.platforms.find(p=>p.id==='mobile');
if(!mobile)throw Error('Confirmed mobile source missing');
c.platforms=[c.platforms.find(p=>p.id==='desktop'),mobile];
c.scope.platforms=['desktop','mobile'];
c.scope.basis='用户先修订端游附件，随后明确“手游也是一样”；端游已交付版保留，手游沿用原确认价格并同步208格及新增注意事项。';
for(const t of c.tiers){
 const original=previous.tiers.find(p=>p.id===t.id);
 if(!original||original.name!==t.name||!Number.isFinite(original.mobilePrice))throw Error('Unverified tier price '+t.id);
 t.mobilePrice=original.mobilePrice;
}
c.sources.request+=' 用户后续明确“手游也是一样”，按原手游价目同步两项修订。';
c.sources.mobilePriceSource='2026-09-30已确认黄金矿工双平台项目content.json：体验288、标准388、基础588、进阶788、豪华1088、终极1488r；本轮用户授权沿用手游版。';
write('content.json',c);
const state=read(path.join(root,'campaign-state.json'));
state.revision='r3';state.stage='vector-revision';
state.contentVerificationBasis=c.sources.request;
state.deliveryScope={pageIds:['desktop','mobile'],platforms:['desktop','mobile'],basis:c.scope.basis};
state.verifiedPricePlatforms=['desktop','mobile'];
state.priceSourceByPlatform.mobile=c.sources.mobilePriceSource;
state.decisions.push('用户后续明确手游同步同一格数和注意事项修订；手游价格沿用既有确认源稿288/388/588/788/1088/1488r。');
state.revisionHistory.push({revision:'r3',date:'2026-10-04',description:'按用户后续请求增加手游同步修订页，端游成图保留。'});
delete state.finalVisualQA;
state.previousDesktopSHA256=read(path.join(root,'review/final-verification.json')).sha256;
write('campaign-state.json',state);
let adapter=fs.readFileSync(path.join(root,'layout-adapter.mjs'),'utf8');
adapter=adapter.replace("revision:'r2'","revision:'r3'");
fs.writeFileSync(path.join(root,'layout-adapter.mjs'),adapter);
console.log('Mobile prices restored from confirmed source; shared revisions retained.');

