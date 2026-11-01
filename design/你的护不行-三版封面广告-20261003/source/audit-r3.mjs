import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const sharp=createRequire(import.meta.url)(path.join(process.argv[2],'sharp'));
const read=async p=>JSON.parse(await fs.readFile(p,'utf8'));
const c=await read(path.join(root,'content.json')),plan=await read(path.join(root,'layout.json'));
const baseline=await read(String.raw`C:\Users\chenh\Documents\ChatGPT\兴安电竞玩法\你的护不行-三版封面广告-20261001\content.json`);
const oldRoot=String.raw`C:\Users\chenh\Documents\ChatGPT\兴安电竞玩法\你的护不行-三版封面广告-20261001`;
const measurements=[],comparison=[],phones=[],details=[];
let count=0;
for(const p of c.products){
 const old=baseline.products.find(o=>o.id===p.id);
 for(const m of p.modes){
  const om=old.modes.find(o=>o.id===m.id);
  if(JSON.stringify(m.tiers)!==JSON.stringify(om.tiers))throw Error('Unexpected tier changes');
  count+=m.tiers.length;
 }
}
for(const [i,p]of plan.pages.entries()){
 const b=await read(path.join(root,`review/${p.id}-text-bounds.json`));
 const title=b.find(t=>t.role==='art-title'),z=plan.regions[p.id].title.safe;
 if(p.id.endsWith('social')){
  const prod=c.products.find(v=>p.id===v.id+'-social');
  for(const mode of prod.modes)for(const [j,tier]of mode.tiers.entries()){
   const group=b.filter(v=>v.group===`price-${mode.id}-${j}`);
   const number=role=>group.find(v=>v.role===role+'-number')?.text;
   if(number('price')!==String(tier.price)||number('guarantee')!==String(tier.guarantee)||number('score')!==String(tier.baseScore??tier.count))throw Error('Tier mapping mismatch '+p.id);
   if(tier.guns){const displayed=group.filter(v=>v.role==='gun-list').flatMap(v=>v.text.split(' · '));if(JSON.stringify(displayed)!==JSON.stringify(tier.guns))throw Error('Gun list mismatch');}
  }
 }
 const [x,y,w,h]=z;
 const margins={left:title.x-x,right:x+w-title.x-title.width,top:title.y-y,bottom:y+h-title.y-title.height};
 if(Object.values(margins).some(n=>n<0))throw Error('Title leaves safety zone '+p.id);
 const collision=b.filter(t=>t!==title&&Math.min(t.x+t.width,title.x+title.width)>Math.max(t.x,title.x)&&Math.min(t.y+t.height,title.y+title.height)>Math.max(t.y,title.y));
 if(collision.length)throw Error('Title overlaps text '+p.id);
 measurements.push({id:p.id,titleWidth:title.width,titleSize:title.effectiveSize,margins,centerOffset:(title.x+title.width/2)-(x+w/2),titleTextCollisions:collision.length});
 if(p.id.endsWith('cover')){
  const old=await read(path.join(oldRoot,`review/${p.id}-text-bounds.json`));
  const ot=old.find(t=>t.role==='art-title');
  measurements.at(-1).widthIncreasePercent=(title.width/ot.width-1)*100;
  comparison.push({input:await sharp(path.join(oldRoot,'output/png',p.id+'.png')).resize(600).png().toBuffer(),left:20+Math.floor(i/2)*620,top:40});
  comparison.push({input:await sharp(path.join(root,'output/png',p.id+'.png')).resize(600).png().toBuffer(),left:20+Math.floor(i/2)*620,top:440});
  const crop=await sharp(path.join(root,'output/png',p.id+'.png')).extract({left:180,top:380,width:1370,height:430}).resize(780).png().toBuffer();
  details.push({input:crop,left:20,top:20+Math.floor(i/2)*265});
 }
 phones.push({input:await fs.readFile(path.join(root,'review',p.id+'-phone.png')),left:15+Math.floor(i/2)*405,top:i%2?280:20});
}
await sharp({create:{width:1880,height:835,channels:4,background:'#E7EDF0'}}).composite(comparison).png().toFile(path.join(root,'review/cover-before-after.png'));
await sharp({create:{width:1230,height:820,channels:4,background:'#E7EDF0'}}).composite(phones).png().toFile(path.join(root,'review/phone-sheet.png'));
await sharp({create:{width:820,height:815,channels:4,background:'#E7EDF0'}}).composite(details).png().toFile(path.join(root,'review/title-details.png'));
await fs.writeFile(path.join(root,'review/title-audit-r3.json'),JSON.stringify({tierChecks:count,latestRevolverFallbackPreserved:!!c.products.find(p=>p.id==='revolver').modes.find(m=>m.id==='confidential').sourceFallback,measurements},null,2));
console.log(JSON.stringify({tierChecks:count,titles:measurements.map(m=>({id:m.id,width:+m.titleWidth.toFixed(2),increase:m.widthIncreasePercent&&+m.widthIncreasePercent.toFixed(1),margins:m.margins}))},null,2));
