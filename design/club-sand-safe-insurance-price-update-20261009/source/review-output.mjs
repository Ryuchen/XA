import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {loadSharp} from './render.mjs';
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const sharp=loadSharp(process.argv.find(v=>v.startsWith('--modules='))?.slice(10));
const read=async f=>JSON.parse(await fs.readFile(path.join(root,f),'utf8'));
const plan=await read('layout.json'),c=await read('content.json'),manifest=await read('output/manifest.json');
const wanted=['mobile-absolute','social'];
if(JSON.stringify(plan.pages.map(p=>p.id))!==JSON.stringify(wanted)||manifest.length!==2)throw Error('Unexpected pages');
for(const dir of ['review/phone-390','review/details'])await fs.mkdir(path.join(root,dir),{recursive:true});
const records=[];
for(const p of plan.pages) {
 const record=manifest.find(r=>r.id===p.id),file=path.join(root,record.png);
 const b=await read(`review/${p.id}-text-bounds.json`),checks=[];
 const values={};
 for(const [role,field]of [['price-number','priceR'],['loot-number','baseGuaranteeW'],['sand-count-part-0','sandSafeCount'],['regular-count-part-0','regularLargeSafeCount']]) {
  const expected=p.sourceRows.map(r=>String(r[field])),actual=b.filter(r=>r.role===role).map(r=>r.text);
  if(JSON.stringify(expected)!==JSON.stringify(actual))throw Error(`${p.id} ${field} source mismatch`);
  values[field]={expected,actual,match:true};
 }
 const pairs=[];
 for(const role of ['price','loot']) {
  const ns=b.filter(v=>v.role===role+'-number'),us=b.filter(v=>v.role===role+'-unit');
  for(const [i,n]of ns.entries()) {
   const u=us[i],gap=u.x-n.x-n.width,baselineDelta=Math.abs(n.baseline-u.baseline);
   const top=Math.min(n.y,u.y),bottom=Math.max(n.y+n.height,u.y+u.height),centerDelta=(top+bottom)/2-n.cy;
   const col=p.alignment[role==='price'?'price':'loot'];
   if(gap<9.9||baselineDelta>.001||Math.abs(centerDelta)>.001||n.x<col.left-.1||u.x+u.width>col.right+.1)throw Error(`${p.id} ${role} alignment failure`);
   pairs.push({role,number:n.text,unit:u.text,gap,baselineDelta,centerDelta,heightRatio:u.height/n.height,numberRight:n.x+n.width,unitX:u.x});
  }
 }
 for(const [i,row]of p.alignment.rows.entries()) {
  const cy=p.id==='mobile-absolute'?[591,701,810][i]:[535,615,695,881,961,1041][i];
  const parts=b.filter(v=>/^(price|loot)-(number|unit)$|^(sand|regular)-count-part-/.test(v.role??'')&&v.cy===cy);
  const top=Math.min(...parts.map(v=>v.y)),bottom=Math.max(...parts.map(v=>v.y+v.height));
  const margins={top:top-row[1],bottom:row[1]+row[3]-bottom};
  if(Math.min(...Object.values(margins))<8)throw Error(`${p.id} table row ${i} too close to edge`);
  checks.push({role:'table-row-'+i,container:row,margins,centerDelta:(top+bottom)/2-(row[1]+row[3]/2),status:'passed'});
 }
 for(const place of plan.placements.filter(v=>v.page===p.id)) {
  const bb=b.filter(v=>(v.role===place.role||v.role?.startsWith(place.role+'-part-'))&&Math.abs((v.cy??v.requestedCenter?.[1])-place.centerY)<.01&&v.x+v.width/2>=place.container[0]&&v.x+v.width/2<=place.container[0]+place.container[2]);
  if(!bb.length)continue;
  const left=Math.min(...bb.map(v=>v.x)),right=Math.max(...bb.map(v=>v.x+v.width)),top=Math.min(...bb.map(v=>v.y)),bottom=Math.max(...bb.map(v=>v.y+v.height));
  const z=place.container;
  const margins={left:left-z[0],right:z[0]+z[2]-right,top:top-z[1],bottom:z[1]+z[3]-bottom};
  if(Math.min(...Object.values(margins))<-.2)throw Error(`${p.id} ${place.role} outside measured safety area`);
  checks.push({role:place.role,container:z,margins,centerDelta:(top+bottom)/2-(z[1]+z[3]/2),status:'passed'});
 }
 const plain=b.filter(v=>!v.includesOutlineAndShadow),overlaps=[];
 for(let i=0;i<plain.length;i++)for(let j=i+1;j<plain.length;j++) {
  const a=plain[i],z=plain[j];
  const dx=Math.min(a.x+a.width,z.x+z.width)-Math.max(a.x,z.x),dy=Math.min(a.y+a.height,z.y+z.height)-Math.max(a.y,z.y);
  if(dx>.2&&dy>.2)overlaps.push({a:a.text,b:z.text,dx,dy});
 }
 if(overlaps.length)throw Error(`${p.id} overlapping glyph boxes ${JSON.stringify(overlaps)}`);
 const sha256=createHash('sha256').update(await fs.readFile(file)).digest('hex');
 const meta=await sharp(file).metadata();
 if(meta.width!==record.width||meta.height!==record.height)throw Error('PNG size mismatch');
 await sharp(file).resize({width:390}).png().toFile(path.join(root,'review/phone-390',p.id+'.png'));
 const specs=p.id==='social'?[['mobile-prices',[46,842,996,242]],['all-prices',[46,405,996,677]],['reward',[46,1097,996,240]]]:[['prices',[44,482,938,387]],['reward',[42,884,942,250]],['notes',[44,1149,938,275]]];
 for(const [name,[left,top,width,height]]of specs) {
  const sx=record.width/p.w,sy=record.height/p.h;
  await sharp(file).extract({left:Math.round(left*sx),top:Math.round(top*sy),width:Math.round(width*sx),height:Math.round(height*sy)}).resize({width:1200}).png().toFile(path.join(root,`review/details/${p.id}-${name}.png`));
 }
 records.push({id:p.id,sha256,dimensions:[meta.width,meta.height],backgroundNative:[record.nativeWidth,record.nativeHeight],values,pairs,checks,overlaps,source:c.sources.mobilePriceUpdate.file});
}
const report={revision:c.revision,status:'geometry-and-source-passed',visualReviewRequired:true,records};
await fs.writeFile(path.join(root,'review/price-and-layout-audit.json'),JSON.stringify(report,null,2)+'\n');
const thumbs=[];
for(const [i,p]of plan.pages.entries())thumbs.push({input:await sharp(path.join(root,'output/png',p.id+'.png')).resize({width:520}).png().toBuffer(),left:32+i*552,top:96});
const mobileRows=c.tiers.find(t=>t.id==='mobile-absolute').rows;
const caption=mobileRows.map(r=>r.priceR).join(' / ')+' 元 · '+mobileRows.map(r=>r.baseGuaranteeW).join(' / ')+' 万';
const title=Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="1136" height="910"><text x="32" y="43" font-family="Microsoft YaHei UI" font-size="26" font-weight="700" fill="#183E50">常规沙保单 · 手游绝密改价</text><text x="32" y="75" font-family="Microsoft YaHei UI" font-size="20" fill="#526570">'+caption+'</text></svg>');
await sharp({create:{width:1136,height:910,channels:4,background:'#F5EFE4'}}).composite([{input:title,left:0,top:0},...thumbs]).png().toFile(path.join(root,'review/update-overview.png'));
console.log(JSON.stringify(records.map(r=>({id:r.id,dataMatched:true,numericPairs:r.pairs.length,maxBaselineDelta:Math.max(...r.pairs.map(p=>p.baselineDelta)),minRowMargin:Math.min(...r.checks.filter(v=>v.role.startsWith('table-row')).flatMap(v=>Object.values(v.margins))),overlaps:r.overlaps.length})),null,2));
