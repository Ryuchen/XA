import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const sharp=createRequire(import.meta.url)(path.join(process.argv[2],'sharp'));
const json=async name=>JSON.parse((await fs.readFile(path.join(root,name),'utf8')).replace(/^\uFEFF/,''));
const plan=await json('layout.json'),manifest=await json('output/manifest.json');
await fs.mkdir(path.join(root,'review/phone-390'),{recursive:true});
await fs.mkdir(path.join(root,'review/price-r2-details'),{recursive:true});
const records=[];
for(const page of plan.pages){
 const png=path.join(root,manifest.find(m=>m.id===page.id).png);
 const meta=await sharp(png).metadata();
 await sharp(png).resize({width:390}).png().toFile(path.join(root,'review/phone-390',page.id+'.png'));
 const top=page.id==='social'?865:503,bottom=page.id==='social'?1185:885;
 const sy=meta.height/page.h,sx=meta.width/page.w;
 await sharp(png).extract({left:Math.round(38*sx),top:Math.round(top*sy),width:Math.round((page.w-76)*sx),height:Math.round((bottom-top)*sy)}).resize({width:1200}).png().toFile(path.join(root,'review/price-r2-details',page.id+'-prices.png'));
 const bounds=await json('review/'+page.id+'-text-bounds.json');
 const numberBounds=bounds.filter(b=>b.role==='price-number'||b.role==='guarantee-number');
 const groups=page.id==='social'?page.alignment:[page.alignment];
 const rows=[];
 for(const n of numberBounds){
  const role=n.role.replace('-number','-unit');
  const u=bounds.find(b=>b.role===role&&b.lineGroup===n.lineGroup&&b.x>n.x&&Math.abs(b.x-(n.x+n.width))<20);
  if(!u)throw Error('Missing paired unit '+page.id+' '+n.text);
  const group=groups.find(g=>g.rowCenters.some(c=>Math.abs(c-(n.y+n.height/2))<1));
  if(!group)throw Error('Row is not centered '+page.id+' '+n.text);
  const row=group.rowCenters.findIndex(c=>Math.abs(c-(n.y+n.height/2))<1);
  const inkTop=Math.min(n.y,u.y),inkBottom=Math.max(n.y+n.height,u.y+u.height);
  const gap=u.x-(n.x+n.width),baselineDelta=Math.abs(n.baseline-u.baseline);
  const wholeRow=bounds.filter(b=>b.lineGroup===n.lineGroup&&/^(price|guarantee)-(number|unit)$/.test(b.role));
  const rowInkTop=Math.min(...wholeRow.map(b=>b.y)),rowInkBottom=Math.max(...wholeRow.map(b=>b.y+b.height));
  const centerDelta=Math.abs((rowInkTop+rowInkBottom)/2-group.rowCenters[row]);
  const topMargin=inkTop-group.rowEdges[row],bottomMargin=group.rowEdges[row+1]-inkBottom;
  if(gap<5.9||baselineDelta>.1||centerDelta>.1||topMargin<6||bottomMargin<6)throw Error('Price geometry failed '+page.id+' '+n.text);
  rows.push({role:n.role,value:n.text,unit:u.text,baselineDelta,visibleGap:gap,centerDelta,topMargin,bottomMargin});
 }
 records.push({pageId:page.id,png:manifest.find(m=>m.id===page.id).png,width:meta.width,height:meta.height,rows});
}
const panels=await Promise.all(plan.pages.map(async p=>{const buffer=await sharp(path.join(root,'output/png',p.id+'.png')).resize({width:390}).toBuffer();const meta=await sharp(buffer).metadata();return {buffer,height:meta.height};}));
await sharp({create:{width:1230,height:Math.max(...panels.map(p=>p.height))+40,channels:4,background:'#f8f7f0'}}).composite(panels.map((p,i)=>({input:p.buffer,left:20+i*410,top:20}))).png().toFile(path.join(root,'review/price-r2-overview.png'));
await fs.writeFile(path.join(root,'review/price-r2-audit.json'),JSON.stringify({revision:'excess-mobile-price-r2-20261009',scope:plan.pages.map(p=>p.id),records},null,2)+'\n');
console.log(JSON.stringify({pages:records.map(r=>({page:r.pageId,size:[r.width,r.height],numberPairs:r.rows.length})),phoneWidth:390}));
