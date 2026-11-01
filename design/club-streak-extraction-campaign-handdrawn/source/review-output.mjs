import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const sharp=createRequire(path.resolve(path.dirname(process.execPath),'..','runtime.cjs'))('sharp');
const read=async f=>JSON.parse(await fs.readFile(path.join(root,f),'utf8'));
const plan=await read('layout.json'),content=await read('content.json'),manifest=await read('output/manifest.json'),m=await read('02-layouts/generated-background-measurements.json');
await fs.mkdir(path.join(root,'review/phone-390'),{recursive:true});
await fs.mkdir(path.join(root,'review/details'),{recursive:true});
const notesByRole={'card-rules':'房卡保障','equipment-rules':'随行装备','failed-order-compensation':'炸单补偿','safe-box-settlement':'结算口径','loot-settlement':'物资说明','quick-extraction':'打手保障','extraction-uplift':'撤离点'};
const audits=[];
for(const p of plan.pages){
 const record=manifest.find(v=>v.id===p.id);if(!record)throw Error('缺少 '+p.id);
 const file=path.join(root,record.png);
 await sharp(file).resize({width:390}).png().toFile(path.join(root,'review/phone-390',p.id+'.png'));
 const bounds=await read('review/'+p.id+'-text-bounds.json');
 const pairs=[];
 for(const role of ['price','loot','compensation']){
  const numbers=bounds.filter(v=>v.role===role+'-number'),units=bounds.filter(v=>v.role===role+'-unit');
  numbers.forEach((v,i)=>{const u=units[i];pairs.push({role,number:v.text,unit:u.text,unitX:u.x,gap:u.x-v.x-v.width,verticalCenterDelta:(v.y+v.height/2)-(u.y+u.height/2),visibleHeightRatio:u.height/v.height});});
 }
 const source=p.id.includes('social')?p.sourceTiers:content.tiers.find(t=>t.id===p.id)?.maps.flatMap(g=>g.rows);
 const dataMatch=source?JSON.stringify(bounds.filter(v=>v.role==='price-number').map(v=>v.text))===JSON.stringify(source.map(v=>String(v.priceR)))&&JSON.stringify(bounds.filter(v=>v.role==='loot-number').map(v=>v.text))===JSON.stringify(source.map(v=>String(v.baseGuaranteeW))):true;
 if(!dataMatch)throw Error(p.id+'实际渲染文字与源数据不一致');
 const margins=[];
 if(p.id.includes('absolute')||p.id.includes('secret')){
  const textBlocks=plan.placements.filter(v=>v.page===p.id);
  for(const block of textBlocks){
   const noteName=notesByRole[block.role];const parent=noteName?m.poster.notes.find(n=>n.id===noteName).bounds:block.role==='valid-streak'?[42,946,639,150]:block.role==='failure-reset'?[42,1108,639,78]:block.role==='completion'?[42,274,639,73]:null;
   if(parent){const bb=bounds.filter(v=>v.role===block.role),top=Math.min(...bb.map(v=>v.y)),bottom=Math.max(...bb.map(v=>v.y+v.height));margins.push({role:block.role,top:top-parent[1],bottom:parent[1]+parent[3]-bottom,groupCenterDelta:(top+bottom)/2-(parent[1]+parent[3]/2)});}
  }
  const comp=bounds.filter(v=>['compensation-item','compensation-number','compensation-unit','compensation-cause'].includes(v.role));
  const card=m.poster.notes.find(n=>n.id==='缺项补偿').bounds,top=Math.min(...comp.map(v=>v.y)),bottom=Math.max(...comp.map(v=>v.y+v.height));
  margins.push({role:'missing-compensation-group',top:top-card[1],bottom:card[1]+card[3]-bottom,groupCenterDelta:(top+bottom)/2-(card[1]+card[3]/2)});
  for(const [mi,group] of m.poster.maps.entries())for(const [ri,edge] of group.rowEdges.slice(0,-1).entries()){
   const cy=(edge+group.rowEdges[ri+1])/2,bb=bounds.filter(v=>['price-number','price-unit','loot-number','loot-unit','streak'].includes(v.role)&&Math.abs(v.cy-cy)<.01);
   margins.push({role:'map-'+mi+'-row-'+ri,top:Math.min(...bb.map(v=>v.y))-edge,bottom:group.rowEdges[ri+1]-Math.max(...bb.map(v=>v.y+v.height))});
  }
 }
 audits.push({page:p.id,label:p.label,dataMatch,sourceTierCount:source?.length??0,pairs,margins,bodyMinimumPhoneSize:p.alignment?.phoneBodyMinSize,ordinaryBodyBold:p.elements.filter(e=>e.type==='text'&&['card-rules','equipment-rules','failed-order-compensation','safe-box-settlement','loot-settlement','quick-extraction','extraction-uplift','rule-label'].includes(e.role)).every(e=>e.style===1)});
 if(p.id==='desktop-absolute'||p.id==='desktop-secret'){
  const temp=await sharp(file).resize(p.w*2,p.h*2,{fit:'fill'}).png().toBuffer();
  await sharp(temp).extract({left:84,top:2476,width:1278,height:1648}).png().toFile(path.join(root,'review/details',p.id+'-notes.png'));
  await sharp(temp).extract({left:84,top:730,width:1278,height:1038}).png().toFile(path.join(root,'review/details',p.id+'-prices.png'));
 }
}
await fs.writeFile(path.join(root,'review/typesetting-audit.json'),JSON.stringify({revision:'streak-r3-route',userPreference:'缩小文字，保留上下间距；默认允许航天飞升和巴克什飞鼠，禁止对应撤离点才加价25%',records:audits},null,2));
const summary=audits.map(a=>({page:a.page,tiers:a.sourceTierCount,dataMatch:a.dataMatch,ordinaryBodyBold:a.ordinaryBodyBold,minPadding:a.margins.length?Math.min(...a.margins.map(v=>Math.min(v.top,v.bottom))):null,unitRatioRange:a.pairs.length?[Math.min(...a.pairs.map(v=>v.visibleHeightRatio)),Math.max(...a.pairs.map(v=>v.visibleHeightRatio))]:null,maxUnitCenterDelta:a.pairs.length?Math.max(...a.pairs.map(v=>Math.abs(v.verticalCenterDelta))):null}));
console.log(JSON.stringify(summary,null,2));
const W=1880,H=2040,composites=[];
const thumb=async(id,left,top,width,height)=>{const file=path.join(root,manifest.find(v=>v.id===id).png);composites.push({input:await sharp(file).resize(width,height,{fit:'fill'}).png().toBuffer(),left,top});};
for(const [i,id] of ['desktop-absolute','desktop-secret','mobile-absolute','mobile-secret'].entries())await thumb(id,40+i*460,150,420,1260);
await thumb('cover',40,1520,770,433);await thumb('desktop-social',895,1500,345,460);await thumb('mobile-social',1400,1500,345,460);
const tx=(x,y,s,size=29,anchor='middle')=>'<text x="'+x+'" y="'+y+'" text-anchor="'+anchor+'" dominant-baseline="central" font-family="Microsoft YaHei UI" font-size="'+size+'" font-weight="700" fill="#203F53">'+s+'</text>';
let labels=tx(42,46,'连续撤离单｜字号与留白修订版',42,'start')+tx(42,95,'7张成品 · 文案为矢量 · r/w固定列 · 价格与规则按本轮确认内容',27,'start');
['端游 · 绝密','端游 · 机密','手游 · 绝密','手游 · 机密'].forEach((s,i)=>labels+=tx(250+i*460,127,s,28));
labels+=tx(425,1480,'横版封面',29)+tx(1067,1460,'端游 · 简洁宣发',29)+tx(1572,1460,'手游 · 简洁宣发',29);
const board=await sharp({create:{width:W,height:H,channels:3,background:'#F5F8FA'}}).composite(composites).png().toBuffer();
await sharp(board).composite([{input:Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="'+W+'" height="'+H+'">'+labels+'</svg>')}]).png().toFile(path.join(root,'review/final-overview.png'));
const cards=plan.pages.map(p=>{const r=manifest.find(v=>v.id===p.id);return '<article><h2>'+p.label+'</h2><nav><a href="../'+r.png+'">PNG '+r.width+'×'+r.height+'</a><a href="../'+r.outlinedSVG+'">转曲SVG</a><a href="../'+r.editableSVG+'">可改文字SVG</a><a href="phone-390/'+p.id+'.png">390px预览</a></nav><a href="../'+r.png+'"><img src="'+p.id+'.png" alt="'+p.label+'" loading="lazy"></a></article>';}).join('');
const html='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>连续撤离单 · 字号留白修订版</title><style>body{margin:0;background:#F5F8FA;color:#203F53;font:16px/1.7 "Microsoft YaHei UI",sans-serif}main{max-width:1700px;margin:auto;padding:25px}h1{margin:0}p{color:#516D7E}nav{display:flex;gap:15px;flex-wrap:wrap}a{color:#127D77}.overview{width:100%;height:auto;margin:20px 0}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px}article{background:white;padding:18px;border:1px solid #D5E1E9;border-radius:12px}article img{width:100%;height:auto;margin-top:14px}h2{font-size:22px}@media(max-width:760px){main{padding:12px}.grid{grid-template-columns:1fr}}</style><main><h1>兴安电竞 · 连续撤离单</h1><p>字号与上下留白修订版。共7张：四张完整价目、封面、端游及手游简洁宣发。正文、数值与规则已填入；正文真实粗体，数字和单位分开定位。</p><nav><a href="../output/campaign-streak-r3-png.zip">下载7张PNG</a><a href="../output/campaign-streak-r3-source.zip">下载成品与矢量源文件</a><a href="final-overview.png">成品总览</a><a href="details/desktop-absolute-notes.png">绝密须知局部</a><a href="details/desktop-secret-notes.png">机密须知局部</a></nav><img class="overview" src="final-overview.png" alt="成品总览"><div class="grid">'+cards+'</div><p>可改文字SVG保留正文与价目文字对象，艺术标题为路径；转曲SVG不依赖字体。背景插画是内嵌位图，生成原图尺寸另行记录。</p></main></html>';
await fs.writeFile(path.join(root,'review/index.html'),html);
console.log('最终检查图、390px预览及查看页已生成。');
