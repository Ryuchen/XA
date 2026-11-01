import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';

const project=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const modules=process.argv[2];
if(!modules)throw Error('Pass bundled Node package directory.');
const sharp=createRequire(path.join(modules,'package.json'))('sharp');
const read=async p=>JSON.parse((await fs.readFile(path.join(project,p),'utf8')).replace(/^\uFEFF/,''));
const plan=await read('layout.json'),content=await read('content.json'),manifest=await read('output/manifest.json');
assert.deepEqual(plan.pages.map(p=>p.id),['mobile-absolute','mobile-secret','social']);
assert.deepEqual(manifest.map(p=>p.id),plan.pages.map(p=>p.id));
const pairs={
 'desktop-absolute':[[268,788],[388,1288],[688,2388],[1299,4888]],
 'desktop-secret':[[168,568],[268,868],[468,1468],[868,2668]],
 'mobile-absolute':[[168,888],[268,1688],[368,2288],[468,3088]],
 'mobile-secret':[[128,888],[228,1688],[328,2288],[428,3088]]
};
for(const t of content.tiers)assert.deepEqual(t.rows.map(r=>[r.priceR,r.lootW]),pairs[t.id]);
for(const dir of ['review/phone-390','review/details'])await fs.mkdir(path.join(project,dir),{recursive:true});
const report=[];
for(const p of plan.pages){
 const rec=manifest.find(r=>r.id===p.id),png=await fs.readFile(path.join(project,rec.png));
 const md=await sharp(png).metadata();
 assert.equal(md.width,p.ew);assert.equal(md.height,p.eh);
 assert.deepEqual(await read('review/'+p.id+'-text-overlaps.json'),[]);
 const bounds=await read('review/'+p.id+'-text-bounds.json');
 const containers=p.id==='social'?p.alignment:[{id:p.id,rowCenters:p.alignment.rowCenters,rowEdges:p.alignment.rowEdges}];
 const numbers=p.elements.filter(e=>e.role==='price-number'||e.role==='loot-number');
 const expected=p.id==='social'?content.tiers.flatMap(t=>t.rows.flatMap(r=>[r.priceR,r.lootW])):content.tiers.find(t=>t.id===p.id).rows.flatMap(r=>[r.priceR,r.lootW]);
 assert.deepEqual(numbers.map(e=>Number(e.text)),expected);
 const rows=[];
 for(const [ci,c] of containers.entries())for(let ri=0;ri<c.rowCenters.length;ri++){
  const cy=c.rowCenters[ri],section=p.id==='social'?{left:ci%2?562:53,right:ci%2?1033:524}:{left:48,right:788};
  const row=bounds.filter(b=>['price-number','price-unit','loot-number','loot-unit'].includes(b.role)&&b.lineGroup===`cy:${cy}`&&b.x>=section.left&&b.x+b.width<=section.right);
  assert.equal(row.length,4,`${p.id} ${c.id} row ${ri}`);
  const baseline=row[0].baseline;
  for(const b of row)assert.ok(Math.abs(b.baseline-baseline)<0.001);
  const top=Math.min(...row.map(b=>b.y)),bottom=Math.max(...row.map(b=>b.y+b.height));
  assert.ok(top>c.rowEdges[ri]+8&&bottom<c.rowEdges[ri+1]-8,'Row must leave visible space inside its actual background dividers.');
  assert.ok(Math.abs((top+bottom)/2-cy)<0.1);
  const byRole=role=>row.find(b=>b.role===role);
  const priceGap=byRole('price-unit').x-(byRole('price-number').x+byRole('price-number').width);
  const lootGap=byRole('loot-unit').x-(byRole('loot-number').x+byRole('loot-number').width);
  assert.ok(priceGap>0&&lootGap>0);
  rows.push({tier:c.id,row:ri+1,baseline,topPadding:top-c.rowEdges[ri],bottomPadding:c.rowEdges[ri+1]-bottom,priceGap,lootGap});
 }
 await sharp(png).resize({width:390}).png().toFile(path.join(project,'review/phone-390',p.id+'.png'));
 const crops=p.id==='social'?[{name:'price-cards',left:48,top:392,width:992,height:680},{name:'guarantee',left:48,top:1085,width:991,height:265}]:[{name:'price-guarantee',left:46,top:414,width:744,height:519},{name:'rules',left:46,top:945,width:744,height:777}];
 for(const c of crops){
  const rect={left:Math.round(c.left*p.ew/p.w),top:Math.round(c.top*p.eh/p.h),width:Math.round(c.width*p.ew/p.w),height:Math.round(c.height*p.eh/p.h)};
  await sharp(png).extract(rect).resize({width:Math.min(c.width,1000)}).png().toFile(path.join(project,'review/details',p.id+'-'+c.name+'.png'));
 }
 report.push({page:p.id,png:rec.png,width:md.width,height:md.height,sha256:createHash('sha256').update(png).digest('hex'),rows,overlaps:0});
}
const thumbs=await Promise.all(manifest.map(async m=>({id:m.id,img:await sharp(path.join(project,m.png)).resize({width:m.id==='social'?480:300}).png().toBuffer()})));
const heights=await Promise.all(thumbs.map(async t=>(await sharp(t.img).metadata()).height));
await sharp({create:{width:1120,height:Math.max(...heights)+40,channels:4,background:'#f8f7f0'}}).composite(thumbs.map((t,i)=>({input:t.img,left:[20,340,660][i],top:20}))).png().toFile(path.join(project,'review/update-overview.png'));
await fs.writeFile(path.join(project,'review/price-layout-verification.json'),JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify({pages:report.map(r=>({page:r.page,rows:r.rows.length,dimensions:[r.width,r.height],overlaps:r.overlaps})),scope:'3 pages',pricePairs:'all 16 pairs match sources; mobile 8 pairs match new table',baseline:'all numeric rows share baseline',preview:'review/update-overview.png'},null,2));
