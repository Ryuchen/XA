import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {loadSharp} from './render.mjs';
const project=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const sharp=loadSharp(process.argv[2]);
const readJSON=async name=>JSON.parse((await fs.readFile(path.join(project,name),'utf8')).replace(/^\uFEFF/,''));
const content=await readJSON('content.json'),plan=await readJSON('layout.json');
const prior=JSON.parse(await fs.readFile(path.resolve(project,'../club-challenge-campaign-handdrawn/content.json'),'utf8'));
const expected={
 'mobile-absolute':{'航天':[[248,1088],[428,1288],[728,1388]],'巴克什':[[228,888],[388,1088],[588,1188]],'监狱':[[228,888],[388,1088],[588,1188]]},
 'mobile-secret':{'核电站':[[168,588],[248,688],[388,788]],'巴克什':[[188,588],[298,788],[428,888]],'航天':[[198,688],[328,888],[468,988]]},
};
assert.deepEqual(plan.pages.map(p=>p.id),['mobile-absolute','mobile-secret','social']);
assert.deepEqual(content.rules,prior.rules);
assert.deepEqual(content.mechanism,prior.mechanism);
assert.deepEqual(content.tiers.filter(t=>t.platform==='端游'),prior.tiers.filter(t=>t.platform==='端游'));
for(const t of content.tiers.filter(t=>t.platform==='手游'))for(const m of t.maps){
 assert.deepEqual(m.rows.map(r=>[r.priceR,r.thresholdW]),expected[t.id][m.name]);
 assert(m.rows.every(r=>r.comparison==='>'));
}
for(const name of ['phone-390','details'])await fs.mkdir(path.join(project,'review',name),{recursive:true});
const box=bs=>({left:Math.min(...bs.map(b=>b.x)),top:Math.min(...bs.map(b=>b.y)),right:Math.max(...bs.map(b=>b.x+b.width)),bottom:Math.max(...bs.map(b=>b.y+b.height))});
const audits=[];
function checkContainer(id,bs,container,cx,cy){
 const b=box(bs),[left,top,right,bottom]=container;
 const margins={left:b.left-left,right:right-b.right,top:b.top-top,bottom:bottom-b.bottom};
 const center={x:(b.left+b.right)/2-cx,y:(b.top+b.bottom)/2-cy};
 assert(Object.values(margins).every(v=>v>=4),id+' leaves less than 4px container margin');
 assert(Math.abs(center.y)<1,id+' not vertically centered');
 audits.push({id,container,ink:b,margins,centerDeviation:center});
}
async function crop(page,id,rect){
 const [left,top,width,height]=rect,sx=page.ew/page.w,sy=page.eh/page.h;
 await sharp(path.join(project,'output/png',page.id+'.png')).extract({left:Math.round(left*sx),top:Math.round(top*sy),width:Math.round(width*sx),height:Math.round(height*sy)}).resize({width:Math.min(width,1400)}).png().toFile(path.join(project,'review/details',id+'.png'));
}
for(const p of plan.pages){
 const png=path.join(project,'output/png',p.id+'.png'),meta=await sharp(png).metadata();
 assert.equal(meta.width,p.ew);assert.equal(meta.height,p.eh);
 await sharp(png).resize({width:390}).png().toFile(path.join(project,'review/phone-390',p.id+'.png'));
 const bs=await readJSON('review/'+p.id+'-text-bounds.json');
 assert.deepEqual(await readJSON('review/'+p.id+'-text-overlaps.json'),[]);
 if(p.id==='social'){
  for(const card of p.alignment.filter(a=>!a.sharedPriceRows))for(const c of card.cells){
   const selected=bs.filter(b=>c.groups.includes(b.group)),[l,t,r,b]=c.container;
   assert.equal(selected.length,5);
   const bbs=box(selected),cy=(t+b)/2;
   checkContainer(card.id+'-'+c.map+'-'+c.row,selected,c.container,(l+r)/2,cy);
   assert(Math.abs((bbs.left+bbs.right)/2-(l+r)/2)<1);
   for(const group of c.groups){
    const runs=selected.filter(b=>b.group===group).sort((a,b)=>a.x-b.x);
    assert(runs.every(b=>Math.abs(b.baseline-runs[0].baseline)<.1));
    for(let i=1;i<runs.length;i++)assert(runs[i].x-(runs[i-1].x+runs[i-1].width)>2);
   }
   const data=content.tiers.find(t=>t.id===card.id).maps.find(m=>m.name===c.map).rows[c.row];
   assert.deepEqual(selected.filter(b=>b.group===c.groups[0]).map(b=>b.text),[String(data.priceR),'r']);
   assert.deepEqual(selected.filter(b=>b.group===c.groups[1]).map(b=>b.text),['>',String(data.thresholdW),'w']);
  }
  await crop(p,'social-mobile-prices',[45,780,996,296]);
  await crop(p,'social-top-and-desktop',[40,45,1005,726]);
  await crop(p,'social-guarantee',[50,1090,990,245]);
 }else{
  for(const [mi,a]of p.alignment.entries())for(const [ri,cy]of a.rowCenters.entries()){
   const selected=bs.filter(b=>['price-number','price-unit','strict-comparison','threshold-number','threshold-unit'].includes(b.role)&&Math.abs(b.cy-cy)<15);
   assert.equal(selected.length,5);
   checkContainer(p.id+'-'+a.map+'-'+ri,selected,[60,a.rowEdges[ri],776,a.rowEdges[ri+1]],418,cy);
   assert(selected.every(b=>Math.abs(b.baseline-selected[0].baseline)<.1));
   for(const role of ['price','threshold']){
    const n=selected.find(b=>b.role===role+'-number'),u=selected.find(b=>b.role===role+'-unit');
    assert(u.x-(n.x+n.width)>7.9);
   }
   const row=content.tiers.find(t=>t.id===p.id).maps[mi].rows[ri];
   assert.equal(selected.find(b=>b.role==='price-number').text,String(row.priceR));
   assert.equal(selected.find(b=>b.role==='threshold-number').text,String(row.thresholdW));
   assert.equal(selected.find(b=>b.role==='strict-comparison').text,'>');
  }
  await crop(p,p.id+'-prices',[42,430,751,640]);
  await crop(p,p.id+'-rules',[42,1187,751,544]);
 }
}
await fs.writeFile(path.join(project,'review/price-and-layout-audit.json'),JSON.stringify({checkedPages:plan.pages.map(p=>p.id),changedPrices:18,desktopRowsRetained:18,unchangedRules:true,rowsAndCells:audits},null,2));
const thumbs=[];
for(const p of plan.pages){
 const width=p.id==='social'?810:572;
 const data=await sharp(path.join(project,'output/png',p.id+'.png')).resize({width}).png().toBuffer();
 thumbs.push({id:p.id,data,...await sharp(data).metadata()});
}
const social=thumbs.find(t=>t.id==='social'),mobiles=thumbs.filter(t=>t.id!=='social');
await sharp({create:{width:1986,height:1360,channels:4,background:'#EAF4F8'}}).composite([{input:social.data,left:10,top:10},{input:mobiles[0].data,left:836,top:30},{input:mobiles[1].data,left:1414,top:30}]).png().toFile(path.join(project,'review/updated-overview.png'));
console.log(JSON.stringify({changedPrices:18,unchangedRules:true,containersChecked:audits.length,minMargin:Math.min(...audits.flatMap(a=>Object.values(a.margins))),maxVerticalDeviation:Math.max(...audits.map(a=>Math.abs(a.centerDeviation.y))),preview:'review/updated-overview.png'}));
