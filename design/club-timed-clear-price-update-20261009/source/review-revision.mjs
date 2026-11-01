import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {loadSharp} from './render.mjs';
const project=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const sharp=loadSharp(process.argv[2]);
const read=async n=>JSON.parse((await fs.readFile(path.join(project,n),'utf8')).replace(/^\uFEFF/,''));
const content=await read('content.json'),plan=await read('layout.json');
const prior=JSON.parse(await fs.readFile(path.resolve(project,'../club-timed-clear-campaign-handdrawn/content.json'),'utf8'));
const expected={'mobile-absolute':[108,188,328,468,668,888],'mobile-secret':[88,128,188,288,468,688]};
assert.deepEqual(plan.pages.map(p=>p.id),['mobile-absolute','mobile-secret','social']);
assert.deepEqual(content.rules,prior.rules);assert.deepEqual(content.mechanism,prior.mechanism);
assert.deepEqual(content.tiers.filter(t=>t.platform==='端游'),prior.tiers.filter(t=>t.platform==='端游'));
for(const t of content.tiers.filter(t=>t.platform==='手游')){
 assert.deepEqual(t.rows.map(r=>r.priceR),expected[t.id]);
 assert.deepEqual(t.rows.map(r=>r.deadlineMinutes),[null,16,14,12,10,8]);
 for(const r of t.rows)assert.equal(r.materialGuarantee.specified,false);
}
for(const n of ['phone-390','details'])await fs.mkdir(path.join(project,'review',n),{recursive:true});
const box=bs=>({left:Math.min(...bs.map(b=>b.x)),top:Math.min(...bs.map(b=>b.y)),right:Math.max(...bs.map(b=>b.x+b.width)),bottom:Math.max(...bs.map(b=>b.y+b.height))});
const checks=[];
function rowCheck(id,bs,container,cy,kind){
 const ink=box(bs),[left,top,right,bottom]=container;
 const margins={left:ink.left-left,right:right-ink.right,top:ink.top-top,bottom:bottom-ink.bottom};
 assert(Object.values(margins).every(v=>v>3),id+' touches row boundary');
 assert(Math.abs((ink.top+ink.bottom)/2-cy)<.1,id+' is not vertically centered');
 assert(bs.every(b=>Math.abs(b.baseline-bs[0].baseline)<.1),id+' baseline mismatch');
 if(kind!=='regular'){
  const sorted=[...bs].sort((a,b)=>a.x-b.x),gap=sorted[1].x-(sorted[0].x+sorted[0].width);
  assert(gap>5.9,id+' unit gap insufficient');
  checks.push({id,container,ink,margins,verticalDeviation:(ink.top+ink.bottom)/2-cy,baseline:bs[0].baseline,unitGap:gap,unitRatio:sorted[1].height/sorted[0].height});
 }else checks.push({id,container,ink,margins,verticalDeviation:(ink.top+ink.bottom)/2-cy});
}
async function crop(p,id,rect){
 const [left,top,width,height]=rect,sx=p.ew/p.w,sy=p.eh/p.h;
 await sharp(path.join(project,'output/png',p.id+'.png')).extract({left:Math.round(left*sx),top:Math.round(top*sy),width:Math.round(width*sx),height:Math.round(height*sy)}).resize({width:Math.min(width,1400)}).png().toFile(path.join(project,'review/details',id+'.png'));
}
for(const p of plan.pages){
 const png=path.join(project,'output/png',p.id+'.png'),meta=await sharp(png).metadata();
 assert.equal(meta.width,p.ew);assert.equal(meta.height,p.eh);
 await sharp(png).resize({width:390}).png().toFile(path.join(project,'review/phone-390',p.id+'.png'));
 const bs=await read('review/'+p.id+'-text-bounds.json');
 assert.deepEqual(await read('review/'+p.id+'-text-overlaps.json'),[]);
 const cards=p.id==='social'?p.alignment:[{id:p.id,rowEdges:p.alignment.rowEdges,rowCenters:p.alignment.rowCenters,colEdges:[46,440,767],priceUnitColumn:305,deadlineUnitColumn:621}];
 for(const a of cards){
  const tier=content.tiers.find(t=>t.id===a.id);
  for(const [i,cy]of a.rowCenters.entries()){
   const row=tier.rows[i],price=bs.filter(b=>b.lineGroup===`price:${a.priceUnitColumn}:${cy}`),deadline=bs.filter(b=>b.lineGroup===`deadline:${a.deadlineUnitColumn}:${cy}`);
   assert.equal(price.length,2);assert.equal(price.find(b=>b.role==='price-number').text,String(row.priceR));
   const priceContainer=p.id==='social'?[a.colEdges[0],a.rowEdges[i],a.colEdges[1],a.rowEdges[i+1]]:[80,a.rowEdges[i],400,a.rowEdges[i+1]];
   rowCheck(p.id+':'+a.id+':price:'+i,price,priceContainer,cy,'price');
   assert(price.find(b=>b.role==='price-unit').height/price.find(b=>b.role==='price-number').height>=.65);
   assert(price.find(b=>b.role==='price-unit').height/price.find(b=>b.role==='price-number').height<=.75);
   const timeContainer=p.id==='social'?[a.colEdges[1],a.rowEdges[i],a.colEdges[2],a.rowEdges[i+1]]:[460,a.rowEdges[i],742,a.rowEdges[i+1]];
   if(i){assert.equal(deadline.length,2);assert.equal(deadline.find(b=>b.role==='deadline-number').text,String(row.deadlineMinutes));rowCheck(p.id+':'+a.id+':deadline:'+i,deadline,timeContainer,cy,'deadline');}
   else {
    const regular=bs.filter(b=>b.role==='regular-tier'&&Math.abs(b.cy-cy)<.1&&b.x>timeContainer[0]&&b.x<timeContainer[2]);
    assert.equal(regular.length,1);rowCheck(p.id+':'+a.id+':regular',regular,timeContainer,cy,'regular');
   }
  }
 }
 if(p.id==='social'){
  await crop(p,'social-mobile-prices',[49,793,983,350]);
  await crop(p,'social-guarantee',[49,1156,983,214]);
 }else {
  await crop(p,p.id+'-prices',[46,401,722,382]);
  await crop(p,p.id+'-conditions',[46,801,722,510]);
  await crop(p,p.id+'-rules',[46,1321,722,448]);
 }
}
const thumbs=[];
for(const p of plan.pages){const width=p.id==='social'?810:570;thumbs.push({id:p.id,data:await sharp(path.join(project,'output/png',p.id+'.png')).resize({width}).png().toBuffer()});}
await sharp({create:{width:1970,height:1375,channels:4,background:'#EAF4F8'}}).composite([{input:thumbs[2].data,left:10,top:10},{input:thumbs[0].data,left:830,top:10},{input:thumbs[1].data,left:1400,top:10}]).png().toFile(path.join(project,'review/updated-overview.png'));
const audit={pageIds:plan.pages.map(p=>p.id),changedPrices:10,mobileSourceRows:12,mobileRenderedPriceRows:24,desktopRowsRetained:12,unchangedRules:true,unchangedDeadlines:true,fixedMaterialGuaranteeSpecified:false,checks};
await fs.writeFile(path.join(project,'review/price-and-layout-audit.json'),JSON.stringify(audit,null,2));
console.log(JSON.stringify({checkedRows:checks.length,changedPrices:10,minMargin:Math.min(...checks.flatMap(c=>Object.values(c.margins))),maxVerticalDeviation:Math.max(...checks.map(c=>Math.abs(c.verticalDeviation))),overview:'review/updated-overview.png'}));
