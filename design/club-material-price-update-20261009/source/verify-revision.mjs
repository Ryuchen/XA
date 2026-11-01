import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import assert from 'node:assert/strict';
const project=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const sharp=createRequire(path.resolve(path.dirname(process.execPath),'../runtime.cjs'))('sharp');
const read=async p=>JSON.parse(await fs.readFile(path.join(project,p),'utf8'));
const content=await read('content.json'),plan=await read('layout.json');
const result=[];
for(const p of plan.pages){
 const tiers=p.id==='social'?content.tiers:content.tiers.filter(t=>t.id===p.id);
 for(const [role,field] of [['price-number','prices'],['loot-number','loot']])assert.deepEqual(p.elements.filter(e=>e.role===role).map(e=>+e.text),tiers.flatMap(t=>t[field]));
 const bounds=await read(`review/${p.id}-text-bounds.json`);
 const rows=p.id==='social'?p.alignment.flatMap(c=>c.rowCenters):p.alignment.rowCenters;
 for(const role of ['price','loot']){
  const numbers=bounds.filter(b=>b.role===role+'-number'),units=bounds.filter(b=>b.role===role+'-unit');
  numbers.forEach((n,i)=>{const u=units[i];assert.ok(Math.abs(n.baseline-u.baseline)<.001);assert.ok(u.x-n.x-n.width>=8.99);const center=(Math.min(n.y,u.y)+Math.max(n.y+n.height,u.y+u.height))/2;assert.ok(Math.abs(center-rows[i])<3);});
 }
 const png=path.join(project,'output/png',p.id+'.png');
 const meta=await sharp(png).metadata();assert.equal(meta.width,p.ew);assert.equal(meta.height,p.eh);
 await sharp(png).resize({width:390}).png().toFile(path.join(project,'review',p.id+'-phone-390.png'));
 const scale=meta.width/p.w;
 const crop=p.id==='social'?{left:49,top:966,width:990,height:269}:{left:42,top:582,width:726,height:476};
 const rect=Object.fromEntries(Object.entries(crop).map(([k,v])=>[k,Math.round(v*scale)]));
 await sharp(png).extract(rect).resize({width:crop.width}).png().toFile(path.join(project,'review',p.id+'-prices-detail.png'));
 result.push({page:p.id,pricePairs:tiers.length*6,baselineAndGap:'pass',priceCenterTolerance:3,output:[meta.width,meta.height],source:'actual output/png'});
}
await fs.writeFile(path.join(project,'review/data-and-layout-check.json'),JSON.stringify(result,null,2));
console.log(JSON.stringify(result));
