import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
const project=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const sharp=createRequire(path.resolve(path.dirname(process.execPath),'../runtime.cjs'))('sharp');
const read=async p=>JSON.parse(await fs.readFile(path.join(project,p),'utf8'));
const content=await read('content.json'),plan=await read('layout.json');
const expected={
 'mobile-absolute':{priceR:[128,238,368,488,968],lootW:[1188,2188,3188,4188,8188],roundLimit:[3,6,9,12,24],lateBonusW:[488,1288,2088,2888,6088]},
 'mobile-secret':{priceR:[128,238,368,488,968],lootW:[1488,2688,3888,5088,9888],roundLimit:[6,12,18,24,48],lateBonusW:[1088,1888,2688,3488,6688]}
};
for(const [id,fields] of Object.entries(expected))for(const [field,values] of Object.entries(fields))assert.deepEqual(content.tiers.find(t=>t.id===id).rows.map(r=>r[field]),values);
assert.deepEqual(content.topUp.byPlatform.mobile,{'绝密':{priceR:108,additionalLootW:1388},'机密':{priceR:88,additionalLootW:1088}});
const results=[];
for(const p of plan.pages){
 const tiers=p.id==='social'?['desktop-absolute','desktop-secret','mobile-absolute','mobile-secret'].map(id=>content.tiers.find(t=>t.id===id)):[content.tiers.find(t=>t.id===p.id)];
 const b=await read(`review/${p.id}-text-bounds.json`);
 for(const [role,field] of [['price','priceR'],['loot','lootW'],['rounds','roundLimit'],['late-bonus','lateBonusW']]){
  assert.deepEqual(p.elements.filter(e=>e.role===role+'-number').map(e=>+e.text),tiers.flatMap(t=>t.rows.map(r=>r[field])));
  const nums=b.filter(e=>e.role===role+'-number'),units=b.filter(e=>e.role===role+'-unit');
  nums.forEach((n,i)=>{const u=units[i];assert.ok(Math.abs(n.baseline-u.baseline)<.001);assert.ok(u.x-n.x-n.width>=(p.id==='social'?4.99:6.99));});
 }
 if(p.id!=='social'){
  const mode=tiers[0].mode,top=content.topUp.byPlatform.mobile[mode];
  assert.equal(+p.elements.find(e=>e.role==='topup-price-number').text,top.priceR);
  assert.equal(+p.elements.find(e=>e.role==='topup-loot-number').text,top.additionalLootW);
 }
 const png=path.join(project,'output/png',p.id+'.png'),meta=await sharp(png).metadata();
 assert.equal(meta.width,p.ew);assert.equal(meta.height,p.eh);
 await sharp(png).resize({width:390}).png().toFile(path.join(project,'review',p.id+'-phone-390.png'));
 const crop=p.id==='social'?{left:56,top:946,width:974,height:266}:{left:45,top:563,width:747,height:536};
 const rect=Object.fromEntries(Object.entries(crop).map(([k,v])=>[k,Math.round(v*meta.width/p.w)]));
 await sharp(png).extract(rect).resize({width:crop.width}).png().toFile(path.join(project,'review',p.id+'-detail.png'));
 results.push({page:p.id,rows:tiers.length*5,numericColumns:4,data:'pass',baseline:'pass',unitGaps:'pass',output:[meta.width,meta.height]});
}
await fs.writeFile(path.join(project,'review/data-and-layout-check.json'),JSON.stringify(results,null,2));
console.log(JSON.stringify(results));
