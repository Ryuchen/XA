import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const sharp=createRequire(path.resolve(path.dirname(process.execPath),'..','runtime.cjs'))('sharp');
const read=async f=>JSON.parse(await fs.readFile(path.join(root,f),'utf8'));
const plan=await read('layout.json'),c=await read('content.json'),manifest=await read('output/manifest.json'),m=await read('02-layouts/generated-background-measurements.json');
const all=c.tiers.flatMap(t=>t.rows),records=[];
for(const d of ['review/phone-390','review/details'])await fs.mkdir(path.join(root,d),{recursive:true});
for(const p of plan.pages) {
 const r=manifest.find(v=>v.id===p.id),file=path.join(root,r.png);
 const meta=await sharp(file).metadata();
 await sharp(file).resize({width:390}).png().toFile(path.join(root,'review/phone-390',p.id+'.png'));
 const b=await read(`review/${p.id}-text-bounds.json`),src=p.sourceRows,values={};
 if(src) {
  for(const [role,field] of [['price-number','priceR'],['loot-number','baseGuaranteeW'],['sand-count-part-0','sandSafeCount'],['regular-count-part-0','regularLargeSafeCount']]) {
   const actual=b.filter(v=>v.role===role).map(v=>v.text),expected=src.map(v=>String(v[field]));
   values[field]={expected,actual,match:JSON.stringify(actual)===JSON.stringify(expected)};
   if(!values[field].match)throw Error(p.id+' output values differ from source: '+field);
  }
 }
 const pairs=[];
 for(const role of ['price','loot']) {
  const ns=b.filter(v=>v.role===role+'-number'),us=b.filter(v=>v.role===role+'-unit');
  for(let i=0;i<ns.length;i++) {
   const n=ns[i],u=us[i];
   pairs.push({role,number:n.text,unit:u.text,unitX:u.x,gap:u.x-n.x-n.width,centerDelta:(u.y+u.height/2)-(n.y+n.height/2),heightRatio:u.height/n.height});
  }
 }
 const margins=[];
 for(const [i,row] of (p.alignment.rows??[]).entries()) {
  const cy=row[1]+row[3]/2,bb=b.filter(v=>['price-number','price-unit','loot-number','loot-unit','sand-count-part-0','sand-count-part-1','regular-count-part-0','regular-count-part-1'].includes(v.role)&&Math.abs(v.cy-cy)<2);
  if(bb.length){const top=Math.min(...bb.map(v=>v.y)),bottom=Math.max(...bb.map(v=>v.y+v.height));margins.push({role:'table-row-'+i,top:top-row[1],bottom:row[1]+row[3]-bottom});}
 }
 for(const placement of plan.placements.filter(v=>v.page===p.id&&['reward-extra','reward-gift','reward-conditions','rule-count','rule-proxy','rule-loot','cover-subtitle','cover-scope','social-completion-targets','social-completion-relation'].includes(v.role))) {
  const bb=b.filter(v=>v.role===placement.role||v.role?.startsWith(placement.role+'-part-'));
  if(!bb.length)continue;
  const top=Math.min(...bb.map(v=>v.y)),bottom=Math.max(...bb.map(v=>v.y+v.height)),left=Math.min(...bb.map(v=>v.x)),right=Math.max(...bb.map(v=>v.x+v.width));
  const z=placement.container;
  margins.push({role:placement.role,top:top-z[1],bottom:z[1]+z[3]-bottom,left:left-z[0],right:z[0]+z[2]-right,centerDelta:(top+bottom)/2-(z[1]+z[3]/2)});
 }
 const ordinary=p.elements.flatMap(e=>e.type==='inline'?e.parts:e.type==='text'?[e]:[]).filter(e=>e.font==='Microsoft YaHei UI');
 if(p.id.includes('absolute')) for(const [role,parent] of [['rule-count',[44,1209,937,62]],['rule-proxy',[44,1279,937,62]],['rule-loot',[44,1349,937,74]]]) {
  const bb=b.filter(v=>v.role?.startsWith(role+'-part-')),top=Math.min(...bb.map(v=>v.y)),bottom=Math.max(...bb.map(v=>v.y+v.height));
  margins.push({role:role+'-actual-card',top:top-parent[1],bottom:parent[1]+parent[3]-bottom,actualGlyphHeight:bottom-top});
 }
 records.push({id:p.id,pngSize:[meta.width,meta.height],native:[r.nativeWidth,r.nativeHeight],tiers:src?.length??0,values,pairs,margins,normalTextRuns:ordinary.filter(v=>v.style===0).length,boldTextRuns:ordinary.filter(v=>v.style===1).length,minimumRulePhoneFont:p.alignment.minimumPhoneBodySize??null,artTitles:b.filter(v=>v.includesOutlineAndShadow)});
 if(p.id!=='cover') {
  const specs=p.id==='social'?[['prices',{left:46,top:405,width:996,height:677}],['reward',{left:46,top:1097,width:996,height:240}]]:[['prices',{left:44,top:482,width:938,height:387}],['reward',{left:42,top:884,width:942,height:250}],['notes',{left:44,top:1149,width:938,height:275}]];
  for(const [name,box] of specs) {
   const sx=r.width/p.w,sy=r.height/p.h;
   const rect={left:Math.round(box.left*sx),top:Math.round(box.top*sy),width:Math.round(box.width*sx),height:Math.round(box.height*sy)};
   await sharp(file).extract(rect).resize({width:1200}).png().toFile(path.join(root,`review/details/${p.id}-${name}.png`));
  }
 }
}
await fs.writeFile(path.join(root,'review/typesetting-audit.json'),JSON.stringify({revision:plan.version,note:'源数据与文字位置辅助检查；需配合实际PNG目检，不替代视觉验收。',records},null,2)+'\n');
console.log(JSON.stringify(records.map(r=>({id:r.id,tiers:r.tiers,png:r.pngSize,dataMatch:Object.values(r.values).every(v=>v.match),normalRuns:r.normalTextRuns,boldRuns:r.boldTextRuns,unitRatio:r.pairs.length?[Math.min(...r.pairs.map(v=>v.heightRatio)),Math.max(...r.pairs.map(v=>v.heightRatio))]:null,minimumTablePadding:Math.min(...r.margins.filter(v=>v.role.startsWith('table-row')).map(v=>Math.min(v.top,v.bottom))),phoneRuleSize:r.minimumRulePhoneFont})),null,2));
const W=1480,H=740,xs=[32,400,768,1136],layers=[];
const label=(x,y,text,size=22)=>`<text x="${x}" y="${y}" text-anchor="middle" font-family="Microsoft YaHei UI" font-size="${size}" font-weight="700" fill="#183E50">${text}</text>`;
let txt=`<rect width="${W}" height="${H}" fill="#F5EFE4"/><text x="38" y="43" font-family="Microsoft YaHei UI" font-size="29" font-weight="700" fill="#183E50">兴安电竞 · 沙色保险单</text><text x="38" y="76" font-family="Microsoft YaHei UI" font-size="18" fill="#526570">4张成品｜绝密端手游｜正文矢量填入｜重点选择性加粗｜r/w固定列</text>`;
const labels=['端游 · 绝密','手游 · 绝密','横版封面','双平台简洁宣发'];
for(const [i,p] of plan.pages.entries()) {
 const r=manifest.find(v=>v.id===p.id),thumb=await sharp(path.join(root,r.png)).resize({width:314}).png().toBuffer(),tm=await sharp(thumb).metadata();
 layers.push({input:thumb,left:xs[i]+12,top:143});txt+=label(xs[i]+169,122,labels[i])+label(xs[i]+169,tm.height+179,`${r.width}×${r.height}`,17);
}
await sharp({create:{width:W,height:H,channels:4,background:'#F5EFE4'}}).composite([{input:Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}">${txt}</svg>`),left:0,top:0},...layers]).png().toFile(path.join(root,'review/final-overview.png'));
const cards=manifest.map(r=>`<article><h2>${r.label}</h2><nav><a href="../${r.png}">PNG ${r.width}×${r.height}</a><a href="../${r.outlinedSVG}">转曲 SVG</a><a href="../${r.editableSVG}">可改文字 SVG</a><a href="phone-390/${r.id}.png">390px预览</a></nav><img loading="lazy" src="${r.id}.png" alt="${r.label}"></article>`).join('');
const html=`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>兴安电竞 · 沙色保险单</title><style>body{margin:0;background:#F5EFE4;color:#183E50;font:16px/1.7 system-ui,'Microsoft YaHei UI',sans-serif}main{max-width:1480px;margin:auto;padding:26px}h1{font-size:30px;margin-bottom:8px}nav{display:flex;flex-wrap:wrap;gap:16px}a{color:#985F24}img{display:block;width:100%;height:auto;margin:18px auto}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px}article{background:#fffdf7;border:1px solid #D9CDB9;border-radius:16px;padding:18px}h2{font-size:22px}@media(max-width:780px){main{padding:14px}.grid{grid-template-columns:1fr}}</style><main><h1>兴安电竞 · 沙色保险单</h1><p>绝密端手游完整价目各一张、横版封面、双平台简洁宣发，共4张。r/w独立定位，普通说明常规字重，重点真实粗体；三项同时达标结单，三幻神奖励整单一次，赠送不计套餐次数。</p><nav><a href="../output/campaign-sand-safe-insurance-v1-png.zip">下载4张PNG</a><a href="../output/campaign-sand-safe-insurance-v1-source.zip">下载成品与矢量源文件</a><a href="details/desktop-absolute-notes.png">规则局部</a><a href="details/social-reward.png">宣发奖励局部</a></nav><img src="final-overview.png" alt="成品总览"><div class="grid">${cards}</div><p>正文、数值与艺术标题为矢量；背景插画是嵌入位图。转曲SVG不依赖字体，可改文字SVG的正文与数字保留文字对象。PNG文字按目标尺寸直接渲染；底图原生尺寸记录在源文件元数据中，放大导出不等于原生高分辨率绘制。</p></main></html>`;
await fs.writeFile(path.join(root,'review/index.html'),html);
