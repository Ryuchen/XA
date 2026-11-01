import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const read=async f=>JSON.parse((await fs.readFile(f,'utf8')).replace(/^\uFEFF/,''));
const content=await read(path.join(root,'content.json')),state=await read(path.join(root,'campaign-state.json'));
const sourceFile=path.join(root,'references/regular-material-price-source.json');
try{await fs.access(sourceFile);}catch{await fs.copyFile(path.join(path.dirname(root),'club-material-price-update-20261009/content.json'),sourceFile);}
const regular=await read(sourceFile);
const mobileBefore=JSON.stringify(content.tiers.filter(t=>t.platform==='手游'));
const audit=[];
for(const t of content.tiers.filter(t=>t.platform==='端游')){
 const source=regular.tiers.find(s=>s.platform===t.platform&&s.mode===t.mode);
 for(const g of t.maps)for(const r of g.rows){
  const index=source.prices.findIndex(v=>v>=r.priceR);
  if(index<0||r.priceR<source.prices[0])throw Error('Price outside source range');
  const exact=source.prices[index]===r.priceR,low=exact?index:index-1,high=index;
  const referenceGuaranteeW=exact?source.loot[index]:source.loot[low]+(r.priceR-source.prices[low])*(source.loot[high]-source.loot[low])/(source.prices[high]-source.prices[low]);
  const discountedW=referenceGuaranteeW*.7,finalW=Math.floor(discountedW/100)*100+88;
  r.baseGuaranteeW=finalW;
  r.guaranteeCalculation={regularMaterialMode:t.mode,referencePriceR:r.priceR,referenceRangeR:[source.prices[low],source.prices[high]],referenceRangeW:[source.loot[low],source.loot[high]],referenceGuaranteeW,discountFactor:.7,discountedW,suffixPolicy:'retain-hundreds-replace-last-two-with-88',finalW};
  audit.push({platform:t.platform,mode:t.mode,map:g.map,priceR:r.priceR,cumulativeExtractions:r.cumulativeExtractions,...r.guaranteeCalculation});
 }
}
if(JSON.stringify(content.tiers.filter(t=>t.platform==='手游'))!==mobileBefore)throw Error('Mobile data changed');
content.sources.priceSourceByPlatform.desktop='本轮用户给定端游价格；保底依据references/regular-material-price-source.json对应模式常规档位线性折算，乘70%，保留百位及更高位后两位改为88；用户已明确选择此算法。';
content.sources.desktopGuaranteeConfirmation='2026-10-09 用户追加：端游对应保底为同价常规物资单保底七折，88结尾；手游保底不用改。用户确认：无同价档按相邻档位线性折算；保留百位及更高位，后两位改为88。';
content.contentNotes=content.contentNotes.map(s=>s==='手游价格与保底取本轮截图，端游基础保底沿用原表。'?'手游价格与保底取本轮截图；端游保底按对应模式常规物资单同价线性折算值的七折，保留百位及更高位，后两位改88。':s);
content.guaranteePolicy={platform:'desktop',source:'references/regular-material-price-source.json',sameModeRequired:true,useBaseGuaranteeBeforeWeekdayOffer:true,missingPriceMethod:'linear-interpolation',discountFactor:.7,suffix:88,suffixPolicy:'retain-hundreds-replace-last-two-with-88',mobile:'unchanged-user-screenshot',confirmation:content.sources.desktopGuaranteeConfirmation};
state.priceSourceByPlatform=content.sources.priceSourceByPlatform;
state.contentVerificationBasis=content.sources.prices+'；'+content.sources.desktopGuaranteeConfirmation;
state.assumptions=[];state.confirmedCalculationPolicy=content.guaranteePolicy;state.visualQA.status='pending';
for(const [f,data]of [['content.json',content],['campaign-state.json',state],['review/desktop-guarantee-calculations.json',audit]])await fs.writeFile(path.join(root,f),JSON.stringify(data,null,2));
console.log(JSON.stringify(content.tiers.filter(t=>t.platform==='端游').map(t=>({id:t.id,maps:t.maps.map(g=>({map:g.map,prices:g.rows.map(r=>r.priceR),guarantees:g.rows.map(r=>r.baseGuaranteeW)}))})),null,2));
