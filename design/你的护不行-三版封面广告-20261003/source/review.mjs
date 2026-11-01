import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const sharp=createRequire(import.meta.url)(path.join(process.argv[2],'sharp'));
const read=async p=>JSON.parse(await fs.readFile(path.join(root,p),'utf8'));
const c=await read('content.json'),plan=await read('layout.json'),state=await read('campaign-state.json');
const result={pageSet:plan.pages.map(p=>p.id),requested:state.deliveryScope.pageIds,artwork:c.artwork,pages:[],business:[]};
if(JSON.stringify([...result.pageSet].sort())!==JSON.stringify([...result.requested].sort()))throw Error('Page scope mismatch');
const tiles=[];
for(const [i,p]of plan.pages.entries()){
 const png=path.join(root,'output/png',p.id+'.png');
 const b=await read(`review/${p.id}-text-bounds.json`),regions=plan.regions[p.id];
 const over=await read(`review/${p.id}-text-overlaps.json`);
 const outside=b.filter(t=>t.x<0||t.y<0||t.x+t.width>p.w||t.y+t.height>p.h);
 const groups=[];
 for(const [id,r]of Object.entries(regions)){
  const ts=b.filter(t=>t.group===id);if(!ts.length)continue;
  const [x,y,w,h]=r.safe||r.bounds;
  const l=Math.min(...ts.map(t=>t.x)),right=Math.max(...ts.map(t=>t.x+t.width)),top=Math.min(...ts.map(t=>t.y)),bot=Math.max(...ts.map(t=>t.y+t.height));
  groups.push({id,ink:[l,top,right-l,bot-top],safe:[x,y,w,h],inside:l>=x-0.1&&right<=x+w+0.1&&top>=y-0.1&&bot<=y+h+0.1,verticalOffset:(top+bot)/2-(y+h/2),horizontalOffset:(l+right)/2-(x+w/2)});
 }
 const amountGroups=[];for(const t of b.filter(x=>/^(price|guarantee)-number$/.test(x.role))){const u=b.find(x=>x.lineGroup===t.lineGroup&&x.role.endsWith('-unit'));if(!u)throw Error('Missing unit');amountGroups.push({number:t.text,unit:u.text,gap:u.x-(t.x+t.width),baselineDifference:Math.abs(t.baseline-u.baseline),heightRatio:u.height/t.height});}
 const m=await sharp(png).metadata();
 await sharp(png).resize({width:390}).png().toFile(path.join(root,'review',p.id+'-phone.png'));
 const tile=await sharp(png).resize({width:600}).png().toBuffer();
 tiles.push({input:tile,left:40+Math.floor(i/2)*630,top:i%2?430:32});
 if(p.id.endsWith('social'))await sharp(png).extract({left:Math.round(40*p.ew/p.w),top:Math.round(452*p.eh/p.h),width:Math.round(1120*p.ew/p.w),height:Math.round(918*p.eh/p.h)}).resize({width:1400}).png().toFile(path.join(root,'review',p.id+'-pricing.png'));
 result.pages.push({id:p.id,pngDimensions:[m.width,m.height],overlaps:over.length,outside,groups,amountGroups,minimumPhoneSize:Math.min(...b.filter(t=>t.role!=='art-title').map(t=>t.effectiveSize*390/p.w)),smallestText:b.reduce((a,v)=>a.effectiveSize<v.effectiveSize?a:v)});
 const prod=c.products.find(prod=>p.id.startsWith(prod.id));const texts=p.elements.flatMap(e=>e.text?[String(e.text)]:[]);
 if(p.id.endsWith('cover')){for(const banned of ['注意事项','下单须知','撤离失败','补偿','抵消'])if(texts.some(s=>s.includes(banned)))throw Error('Cover contains notice: '+banned);}
 else for(const mo of prod.modes)for(const t of mo.tiers){for(const value of [t.price,t.guarantee,t.baseScore??t.count])if(!texts.includes(String(value)))throw Error('Missing price/benefit '+value);if(t.guns)for(const gun of t.guns)if(!texts.some(s=>s.split(' · ').includes(gun)))throw Error('Missing gun '+gun);result.business.push({product:prod.id,mode:mo.id,tier:t.name,price:t.price,guarantee:t.guarantee,baseScore:t.baseScore,count:t.count,guns:t.guns});}
}
await sharp({create:{width:1940,height:1250,channels:4,background:'#E7EDF0'}}).composite(tiles).png().toFile(path.join(root,'review/contact-sheet.png'));
await fs.writeFile(path.join(root,'review/verification.json'),JSON.stringify(result,null,2));
console.log(JSON.stringify({pages:result.pages.map(p=>({id:p.id,dimensions:p.pngDimensions,overlaps:p.overlaps,outside:p.outside.length,outsideRegions:p.groups.filter(g=>!g.inside),priceCenterOffsets:p.groups.filter(g=>g.id.startsWith('price-')).map(g=>[g.id,Number(g.verticalOffset.toFixed(2))]),minimumPhoneSize:p.minimumPhoneSize})),tierChecks:result.business.length},null,2));
