import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {loadSharp} from './render.mjs';
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url))),sharp=loadSharp();
const read=async f=>JSON.parse((await fs.readFile(path.join(root,f),'utf8')).replace(/^\uFEFF/,''));
const plan=await read('layout.json'),content=await read('content.json'),manifest=await read('output/manifest.json'),metrics=await read('source/background-measurements.json');
for(const dir of ['review/phone-390','review/details'])await fs.mkdir(path.join(root,dir),{recursive:true});
const audits=[],failures=[];
const fail=s=>failures.push(s);
const groupBox=rows=>({x:Math.min(...rows.map(b=>b.x)),y:Math.min(...rows.map(b=>b.y)),right:Math.max(...rows.map(b=>b.x+b.width)),bottom:Math.max(...rows.map(b=>b.y+b.height))});
for(const p of plan.pages){
 const record=manifest.find(v=>v.id===p.id),file=path.join(root,record.png),bounds=await read(`review/${p.id}-text-bounds.json`);
 await sharp(file).resize({width:390}).png().toFile(path.join(root,`review/phone-390/${p.id}.png`));
 const source=p.id.includes('social')?p.sourceTiers:content.tiers.find(t=>t.id===p.id)?.maps.flatMap(g=>g.rows);
 const same=(role,field)=>JSON.stringify(bounds.filter(b=>b.role===role).map(b=>b.text))===JSON.stringify(source.map(r=>String(r[field])));
 if(source&&(!same('price-number','priceR')||!same('loot-number','baseGuaranteeW')))fail(p.id+' price/guarantee mismatch');
 const copy=p.elements.map(e=>e.text??e.runs?.map(r=>r.text).join('')??'').join('|');
 if(/连续|连撤|重新计算|也不打断|保底无上限/.test(copy))fail(p.id+' retains old copy');
 if(p.id==='cover'&&/须知|补偿|结算|条件/.test(copy))fail('Cover contains detailed rules');
 const pairs=[];
 for(const role of ['price','loot','compensation','cumulative']){
  const numbers=bounds.filter(v=>v.role===role+'-number'),units=bounds.filter(v=>v.role===role+'-unit');
  numbers.forEach((v,i)=>{const u=units[i];if(!u)return fail(p.id+' missing unit');const gap=u.x-v.x-v.width,baselineDelta=v.baseline-u.baseline; if(gap<4||Math.abs(baselineDelta)>.1)fail(p.id+' unit gap/baseline');pairs.push({role,value:v.text,unit:u.text,gap,baselineDelta,visibleHeightRatio:u.height/v.height});});
 }
 const groups=[];
 for(const pl of plan.placements.filter(v=>v.page===p.id)){
  const bb=bounds.filter(b=>b.role===pl.role);if(!bb.length)continue;const box=groupBox(bb),[x,y,w,h]=pl.container;
  const padding={left:box.x-x,right:x+w-box.right,top:box.y-y,bottom:y+h-box.bottom},delta=(box.y+box.bottom)/2-(y+h/2);
  if(Math.min(...Object.values(padding))<0||Math.abs(delta)>1)fail(p.id+' '+pl.role+' out of safe area or not centered');
  groups.push({role:pl.role,safeArea:pl.container,padding,centerDeltaY:delta});
 }
 const isPoster=/-(absolute|secret)$/.test(p.id);
 const rowPadding=[];
 if(isPoster){
  for(const [mi,g]of metrics.poster.maps.entries())for(const [ri,edge]of g.rowEdges.slice(0,-1).entries()){
   const center=(edge+g.rowEdges[ri+1])/2;
   const bb=bounds.filter(v=>['price-number','price-unit','loot-number','loot-unit','cumulative'].includes(v.role)&&Math.abs(v.cy-center)<5);
   const box=groupBox(bb),top=box.y-edge,bottom=g.rowEdges[ri+1]-box.bottom;
   if(Math.min(top,bottom)<3)fail(p.id+' row has insufficient padding');
   rowPadding.push({map:mi,row:ri,top,bottom,centerDeltaY:(box.y+box.bottom)/2-center});
  }
  const n=metrics.poster.notes.find(n=>n.id==='缺项补偿'),[x,y,w,h]=n.bodySafe,bb=bounds.filter(b=>b.role.startsWith('compensation-')),box=groupBox(bb);
  const padding={left:box.x-x,right:x+w-box.right,top:box.y-(y-96),bottom:y-96+h-box.bottom};
  if(Math.min(...Object.values(padding))<0)fail(p.id+' compensation group out of bounds');
  groups.push({role:'missing-compensation-group',padding,centerDeltaY:(box.y+box.bottom)/2-(y-96+h/2)});
  const sx=record.width/p.w,sy=record.height/p.h;
  for(const [name,rect] of Object.entries({prices:[42,360,639,525],validity:[42,904,639,292],notes:[42,1142,639,824]})){
   await sharp(file).extract({left:Math.round(rect[0]*sx),top:Math.round(rect[1]*sy),width:Math.round(rect[2]*sx),height:Math.round(rect[3]*sy)}).resize({width:1100}).png().toFile(path.join(root,`review/details/${p.id}-${name}.png`));
  }
 }
 const overlaps=await read(`review/${p.id}-text-overlaps.json`);if(overlaps.length)fail(p.id+' overlaps');
 audits.push({id:p.id,priceRows:source?.length??0,dataMatches:true,oldCopyAbsent:true,pairs,groups,rowPadding});
}
await fs.writeFile(path.join(root,'review/data-and-geometry-audit.json'),JSON.stringify({revision:'cumulative-r2',audits,failures},null,2));
if(failures.length)throw Error(failures.join('\n'));
const W=1820,H=1980,composites=[];
const thumb=async(id,left,top,width)=>{const input=await sharp(path.join(root,manifest.find(v=>v.id===id).png)).resize({width}).png().toBuffer();composites.push({input,left,top});};
for(const [i,id]of ['desktop-absolute','desktop-secret','mobile-absolute','mobile-secret'].entries())await thumb(id,30+i*447,125,419);
await thumb('cover',30,1460,800);await thumb('social-desktop',875,1440,340);await thumb('social-mobile',1360,1440,340);
const tx=(x,y,s,size=28,anchor='middle')=>`<text x="${x}" y="${y}" text-anchor="${anchor}" font-family="Microsoft YaHei UI" font-size="${size}" font-weight="700" fill="#203F53">${s}</text>`;
let labels=tx(30,45,'兴安电竞 · 累计撤离单',38,'start')+tx(30,83,'2026-10-09 · 新价目 · 保留有效撤离要求 · 共7张',24,'start');
['端游 · 绝密','端游 · 机密','手游 · 绝密','手游 · 机密'].forEach((s,i)=>labels+=tx(239+i*447,115,s,26));
labels+=tx(430,1440,'横版封面',26)+tx(1045,1416,'端游广告页',26)+tx(1530,1416,'手游广告页',26);
await sharp({create:{width:W,height:H,channels:3,background:'#F6F8FA'}}).composite([...composites,{input:Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}">${labels}</svg>`)}]).png().toFile(path.join(root,'review/final-overview.png'));
const cards=plan.pages.map(p=>{const r=manifest.find(v=>v.id===p.id);return `<article><h2>${p.label}</h2><nav><a href="../${r.png}" download>PNG ${r.width}×${r.height}</a><a href="../${r.outlinedSVG}" download>转曲SVG</a><a href="../${r.editableSVG}" download>可改文字SVG</a><a href="phone-390/${p.id}.png">手机预览</a></nav><img src="${p.id}.png" alt="${p.label}" loading="lazy"></article>`;}).join('');
await fs.writeFile(path.join(root,'review/index.html'),`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>兴安电竞 · 累计撤离单</title><style>body{margin:0;background:#F6F8FA;color:#203F53;font:16px/1.7 "Microsoft YaHei UI",sans-serif}main{max-width:1550px;margin:auto;padding:24px}a{color:#117C72}nav{display:flex;flex-wrap:wrap;gap:16px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px}article{background:white;padding:18px;border:1px solid #D6E2EA;border-radius:12px}img{width:100%;height:auto}h2{font-size:21px}.overview{margin:24px 0}@media(max-width:720px){.grid{grid-template-columns:1fr}main{padding:12px}}</style><main><h1>兴安电竞 · 累计撤离单</h1><p>7张成品：封面、两端广告页、四张完整价格表。手游价格与保底按新截图更新；端游新价格已同步；保底按同价常规物资单线性折算后取七折，保留百位及更高位并将后两位改为88。保留有效撤离要求，删除连撤失败重计说明。</p><nav><a href="../campaign-cumulative-r2.zip" download>下载完整成品及源文件</a><a href="../output/campaign-cumulative-r2-png.zip" download>下载7张PNG</a><a href="final-overview.png">查看总览</a></nav><img class="overview" src="final-overview.png" alt="7张成品总览"><div class="grid">${cards}</div><p>文字与数字为矢量，手绘背景为内嵌位图；可改文字SVG依赖原字体，跨设备保持版式可用转曲SVG。</p></main></html>`);
console.log(JSON.stringify(audits.map(a=>({id:a.id,priceRows:a.priceRows,minimumRowPadding:a.rowPadding.length?Math.min(...a.rowPadding.flatMap(r=>[r.top,r.bottom])):null,maximumBaselineDelta:Math.max(0,...a.pairs.map(v=>Math.abs(v.baselineDelta))),maximumGroupCenterDelta:Math.max(0,...a.groups.map(v=>Math.abs(v.centerDeltaY)))})),null,2));
