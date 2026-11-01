import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
const require=createRequire(import.meta.url);
const sharp=require('C:/Users/chenh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/sharp');
const here=path.dirname(fileURLToPath(import.meta.url)), root=path.dirname(here);
const output=path.join(root,'07-final'), preview=path.join(root,'08-final-review');
for(const dir of ['png','svg-outlined','svg-editable'])await fs.mkdir(path.join(output,dir),{recursive:true});
await fs.mkdir(preview,{recursive:true});
const content=JSON.parse(await fs.readFile(path.join(here,'content.json'),'utf8'));
const ink='#213F53', secondary='#567486', gold='#A96620', teal='#187C78', white='#FFFDF7';
const CN='Microsoft YaHei UI', NUM='Bahnschrift', ART='Noto Sans SC Black';
const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const requests=[], ids=new Map();
function fontRequest(text,font=CN,style=1){
  const key=JSON.stringify([String(text),font,style]);
  if(!ids.has(key)){const id='t'+requests.length;ids.set(key,id);requests.push({id,text:String(text),font,style});}
  return ids.get(key);
}
function page(id,label,bg,w,h,ew,eh){return {id,label,bg,w,h,ew,eh,elements:[]};}
function tx(p,text,x,cy,size=16,{font=CN,style=1,fill=ink,anchor='start',maxWidth,role='copy',stroke,strokeWidth}={}){
  p.elements.push({type:'text',id:fontRequest(text,font,style),text:String(text),x,cy,size,fill,anchor,maxWidth,role,stroke,strokeWidth});
}
function art(p,text,x,cy,size,{maxWidth,fill=ink,shadow='#A4BFD5',outline=white,outlineWidth=3.3,angles=true}={}){
  p.elements.push({type:'art',text,x,cy,size,maxWidth,fill,shadow,outline,outlineWidth,angles,ids:[...text].map(c=>fontRequest(c,ART,0))});
}
function raw(p,markup){p.elements.push({type:'raw',markup});}
function brand(p,x,cy,size=22){
  const k=size/28;
  raw(p,`<g transform="translate(${x} ${cy-size*.52}) scale(${k})" fill="none" stroke="${ink}" stroke-width="2.1" stroke-linejoin="round"><path d="M0 27 L15 0 L31 27 Z M8 22 L15 10 L23 22 Z"/></g>`);
  tx(p,content.brand,x+size*1.35,cy,size,{maxWidth:size*4.1,role:'brand'});
}
function number(p,value,unit,unitX,cy,size,fill,unitSize,role){
  // The unit's LEFT ink edge is a shared vertical alignment axis.
  // Number ink is right-aligned with a fixed gap; both are vertically centered.
  tx(p,value,unitX-9,cy,size,{font:NUM,fill,anchor:'end',role:role+'-number'});
  tx(p,unit,unitX,cy,unitSize,{font:NUM,fill,role:role+'-unit'});
}
function offer(p,{x,y,w,h,color=gold,large=false}){
  if(large){
    art(p,'工作日专享',x+25,y+26,24,{maxWidth:180,fill:ink,outlineWidth:1.5,angles:false});
    tx(p,'累计物资保底',x+25,y+69,35,{fill:ink});
    tx(p,'+20%',x+395,y+57,84,{font:NUM,fill:color,maxWidth:310,role:'offer'});
    tx(p,content.offer.exception,x+25,y+h-15,15,{fill:secondary,style:0});
  }else{
    art(p,'工作日专享',x+24,y+27,23,{maxWidth:172,fill:ink,outlineWidth:1.4,angles:false});
    tx(p,'累计物资保底',x+24,y+67,28,{fill:ink});
    tx(p,'+20%',x+295,y+60,74,{font:NUM,fill:color,maxWidth:216,role:'offer'});
    tx(p,content.offer.exception,x+24,y+h-16,13,{fill:secondary,style:0});
  }
}
function poster(t){
  const color=t.color==='gold'?gold:teal;
  const p=page(t.id,`${t.platform} · ${t.mode}`,`poster-${t.color}.png`,809,1942,3200,7680);
  // A single vector navy header replaces the uneven two-color raster header.
  raw(p,`<defs><linearGradient id="price-header-navy" x1="0" y1="0" x2="1" y2=".55"><stop offset="0" stop-color="#1D4056"/><stop offset="1" stop-color="#294B60"/></linearGradient></defs><path data-role="price-header-background" d="M54 528.5 H755 Q768.5 528.5 768.5 542 V582 H40.5 V542 Q40.5 528.5 54 528.5 Z" fill="url(#price-header-navy)" stroke="#24475D" stroke-width=".6"/>`);
  brand(p,43,89,25);
  art(p,'常规',47,153,55,{maxWidth:157,outlineWidth:3.8});
  art(p,'物资单',45,220,76,{maxWidth:258,outlineWidth:4.8});
  raw(p,'<path d="M48 258 Q157 264 270 254" stroke="#EDBC79" stroke-width="6" opacity=".72" fill="none" stroke-linecap="round"/>');
  tx(p,`${t.platform} · ${t.mode}版`,159,283,22,{fill:white,anchor:'middle',maxWidth:207,role:'platform-mode'});
  tx(p,'累计带出物资',153,332,17,{fill:secondary,anchor:'middle'});
  offer(p,{x:41,y:391,w:727,h:119,color});
  tx(p,'订单价格',206,556,21,{fill:white,anchor:'middle'});
  tx(p,'累计带出物资',549,556,21,{fill:white,anchor:'middle'});
  const rows=[621,699.5,778.5,857.5,936.5,1017.5];
  rows.forEach((cy,i)=>{
    tx(p,String(i+1).padStart(2,'0'),67,cy,13,{font:NUM,fill:'#87A0AF'});
    number(p,t.prices[i],'r',260,cy,44,ink,42,'price');
    number(p,t.loot[i],'w',604,cy,50,color,48,'loot');
  });
  tx(p,'表内为基础累计保底；工作日按上方活动规则执行。',52,1070,10.8,{fill:secondary,style:0,maxWidth:536,role:'table-note'});
  tx(p,'r = 元 / w = 万',756,1070,10.8,{fill:secondary,anchor:'end',style:0,role:'units'});
  art(p,'猛攻保障',201,1101,22,{maxWidth:149,fill:ink,outlineWidth:1.4,angles:false});
  tx(p,'成功撤离时适用',743,1101,13,{fill:secondary,anchor:'end'});
  tx(p,content.guarantee.leftCondition,305,1143,15.5,{anchor:'middle',maxWidth:195});
  tx(p,'或',435,1143,21,{fill:color,anchor:'middle'});
  tx(p,`当局物资 <${content.guarantee.lootThreshold[t.mode]}w`,596,1143,16,{fill:color,anchor:'middle',maxWidth:238});
  tx(p,content.guarantee.effect,201,1189,21.4,{maxWidth:546,role:'guarantee-result'});
  tx(p,content.guarantee.explanation,201,1220,12.7,{fill:secondary,style:0});
  art(p,'下单须知',49,1256,21,{maxWidth:145,outlineWidth:1.2,angles:false});
  tx(p,'物资归属 · 装备权益 · 补偿结算',759,1256,11.5,{fill:secondary,anchor:'end',style:0});
  const boundaries=[1272,1345,1410,1475,1542,1607,1685];
  content.rules[t.mode].forEach((r,i)=>{
    const cy=(boundaries[i]+boundaries[i+1])/2;
    tx(p,r.title,155,cy,15.5,{maxWidth:87,role:'rule-label'});
    const lineHeight=18.5;
    if(i===2){
      const values=t.mode==='绝密'?[[200,300],[300,200]]:[[100,150],[150,100]];
      ['应带未带','未经同意未带'].forEach((reason,j)=>{
        const lineCy=cy+(j-.5)*lineHeight;
        tx(p,reason,256,lineCy,13.4,{style:0,role:'rule-body'});
        tx(p,j===0?'金卡':'背包',362,lineCy,13.4,{style:0,role:'rule-body'});
        number(p,'+'+values[j][0],'w',485,lineCy,16,color,12,'compensation');
        tx(p,j===0?'红卡':'左轮',543,lineCy,13.4,{style:0,role:'rule-body'});
        number(p,'+'+values[j][1],'w',714,lineCy,16,color,12,'compensation');
      });
    }else{
      r.lines.forEach((line,j)=>tx(p,line,256,cy+(j-(r.lines.length-1)/2)*lineHeight,13.4,{style:0,maxWidth:491,fill:ink,role:'rule-body'}));
    }
  });
  [145,400,650].forEach((cx,i)=>tx(p,`${String(i+1).padStart(2,'0')}  ${content.process[i]}`,cx,1733,12.5,{anchor:'middle',maxWidth:198,fill:ink}));
  tx(p,`${content.brand}  ·  ${t.platform} · ${t.mode}版  |  下单前请确认地图及订单规则`,405,1780,11.8,{anchor:'middle',fill:ink,stroke:white,strokeWidth:2});
  p.alignment={priceUnitX:260,lootUnitX:604,priceUnitSize:42,lootUnitSize:48,lootRightEdge:639.02,iconSafeGapAtLeast:35,rowCenters:rows,unitAlignment:'left ink edge',verticalAlignment:'glyph ink center / multiline group center'};
  return p;
}
function cover(){
  const p=page('cover','横版封面','cover.png',1672,941,3200,1800);
  brand(p,95,162,38);
  art(p,'常规物资单',87,348,125,{maxWidth:645,outlineWidth:7});
  raw(p,'<path d="M105 432 Q366 461 689 433" fill="none" stroke="#E4B474" stroke-width="11" stroke-linecap="round"/>');
  tx(p,'累计带出物资',96,497,39,{fill:ink});
  tx(p,'端游 · 手游  /  绝密 · 机密',366,578,31,{fill:white,anchor:'middle',maxWidth:511});
  tx(p,'工作日专享',99,670,26,{fill:ink});
  tx(p,'累计物资保底 +20%',302,670,30,{fill:gold,maxWidth:414});
  tx(p,content.offer.exception,302,699,17,{fill:secondary,style:0});
  return p;
}
function social(){
  const p=page('social','简洁广告海报','social.png',1086,1448,2400,3200);
  brand(p,52,91,30);
  art(p,'常规物资单',46,170,73,{maxWidth:406,outlineWidth:5});
  raw(p,'<path d="M53 222 Q251 236 437 217" fill="none" stroke="#E9B578" stroke-width="7" stroke-linecap="round"/>');
  tx(p,'端游 / 手游 · 四类价目',225,263,23,{fill:ink,maxWidth:256,stroke:white,strokeWidth:3});
  offer(p,{x:46,y:366,w:994,h:112,color:gold,large:true});
  const cards=[{x:49,head:527,sub:572,rows:[610.5,653.5,698.5,743,787,832.5]},
    {x:559,head:527,sub:572,rows:[610.5,653.5,698.5,743,787,832.5]},
    {x:49,head:903,sub:947,rows:[987,1030,1074.5,1119.5,1164.5,1209.5]},
    {x:559,head:903,sub:947,rows:[987,1030,1074.5,1119.5,1164.5,1209.5]}];
  p.alignment=[];
  content.tiers.forEach((t,i)=>{
    const c=cards[i],color=t.color==='gold'?gold:teal;
    tx(p,`${t.platform} · ${t.mode}版`,c.x+286,c.head,27,{fill:white,anchor:'middle',maxWidth:337,role:'platform-mode'});
    tx(p,'订单价格',c.x+115,c.sub,16,{fill:ink,anchor:'middle'});
    tx(p,'累计带出物资',c.x+344,c.sub,16,{fill:color,anchor:'middle'});
    c.rows.forEach((cy,j)=>{
      number(p,t.prices[j],'r',c.x+150,cy,33,ink,30,'price');
      number(p,t.loot[j],'w',c.x+390,cy,36,color,34,'loot');
      raw(p,`<path d="M${c.x+223} ${cy-3} l4 3 -4 3" fill="none" stroke="${color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" opacity=".65"/>`);
    });
    p.alignment.push({card:t.id,priceUnitX:c.x+150,lootUnitX:c.x+390,priceUnitSize:30,lootUnitSize:34,rowCenters:c.rows});
  });
  tx(p,'表内为基础累计物资保底 · 工作日按上方规则加成',544,1264,17,{anchor:'middle',fill:ink,style:0,stroke:white,strokeWidth:2.5});
  tx(p,'r = 元   /   w = 万',544,1298,15,{anchor:'middle',fill:secondary,style:0,stroke:white,strokeWidth:2.5});
  return p;
}
const pages=[...content.tiers.map(poster),cover(),social()];
await fs.writeFile(path.join(here,'text-requests.json'),JSON.stringify(requests,null,2));
const requestFile=path.join(here,'text-requests.json'), outlineFile=path.join(here,'font-outlines.json');
console.log(execFileSync('powershell.exe',['-NoProfile','-ExecutionPolicy','Bypass','-File',path.join(here,'outline-text.ps1'),'-InputFile',requestFile,'-OutputFile',outlineFile],{encoding:'utf8'}).trim());
const glyphs=JSON.parse(await fs.readFile(outlineFile,'utf8'));
const fmt=n=>Number(n.toFixed(4));
const usedBounds=[];
function renderText(e,editable,pageId){
  const g=glyphs[e.id];
  let scale=e.size/100;
  if(e.maxWidth)scale=Math.min(scale,e.maxWidth/g.width);
  const width=g.width*scale, height=g.height*scale;
  const x=e.x-(e.anchor==='middle'?width/2:e.anchor==='end'?width:0);
  const y=e.cy-height/2;
  const dx=x-g.x*scale,dy=y-g.y*scale;
  const stroke=e.stroke?` stroke="${e.stroke}" stroke-width="${fmt(e.strokeWidth/scale)}" stroke-linejoin="round" paint-order="stroke fill"`:'';
  if(!editable)usedBounds.push({page:pageId,text:e.text,role:e.role,x,y,width,height,cy:e.cy,requestedSize:e.size,effectiveSize:fmt(scale*100)});
  const opening=`<g data-text="${esc(e.text)}" data-role="${esc(e.role)}" transform="translate(${fmt(dx)} ${fmt(dy)}) scale(${fmt(scale)})" fill="${e.fill}">`;
  return opening+(editable?`<text x="0" y="${fmt(g.ascent)}" font-family="${esc(g.font)}" font-size="100" font-weight="${g.style===1?'700':'400'}"${stroke}>${esc(e.text)}</text>`:`<path d="${g.d}"${stroke}/>`)+`</g>`;
}
function renderArt(e){
  let size=e.size;
  const glyphWidths=e.ids.map(id=>glyphs[id].width/100);
  const natural=glyphWidths.reduce((a,b)=>a+b,0)+.065*(e.ids.length-1);
  if(e.maxWidth)size=Math.min(size,e.maxWidth/natural);
  let cursor=e.x;
  const angles=[-2.8,1.4,-1.7,2.2,-1.4], bumps=[1,-2,1,-1,0];
  let s=`<g data-art-title="${esc(e.text)}" aria-label="${esc(e.text)}">`;
  e.ids.forEach((id,i)=>{
    const g=glyphs[id],scale=size/100,w=g.width*scale,angle=e.angles?angles[i%5]:0,cy=e.cy+(e.angles?bumps[i%5]*size/70:0);
    const d=`<path d="${g.d}" transform="translate(${fmt(-g.x*scale-w/2)} ${fmt(-(g.y+g.height/2)*scale)}) scale(${fmt(scale)})"/>`;
    const sw=e.outlineWidth/scale;
    s+=`<g transform="translate(${fmt(cursor+w/2)} ${fmt(cy)}) rotate(${angle})"><g transform="translate(${fmt(size*.035)} ${fmt(size*.055)})" fill="${e.shadow}" stroke="${ink}" stroke-width="${fmt(.6/scale)}" stroke-linejoin="round">${d}</g><g fill="${e.fill}" stroke="${e.outline}" stroke-width="${fmt(sw)}" stroke-linejoin="round" paint-order="stroke fill">${d}</g></g>`;
    cursor+=w+size*.065;
  });
  return s+'</g>';
}
const exports=[];
for(const p of pages){
  const data='data:image/png;base64,'+(await fs.readFile(path.join(root,'03-backgrounds',p.bg))).toString('base64');
  const head=`<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="${p.ew}" height="${p.eh}" viewBox="0 0 ${p.w} ${p.h}"><title>${esc(content.brand+' · '+p.label)}</title><desc>二次元手绘底图，矢量标题、正文与价目。以 r 和 w 单位左边缘分别对齐，文字按可见字形垂直居中。</desc><image x="0" y="0" width="${p.w}" height="${p.h}" xlink:href="${data}" preserveAspectRatio="none"/>`;
  for(const editable of [false,true]){
    let svg=head;
    for(const e of p.elements)svg+=e.type==='text'?renderText(e,editable,p.id):e.type==='art'?renderArt(e):e.markup;
    svg+='</svg>';
    await fs.writeFile(path.join(output,editable?'svg-editable':'svg-outlined',p.id+'.svg'),svg);
    if(!editable){
      await sharp(Buffer.from(svg),{limitInputPixels:false}).withMetadata({density:300}).png({compressionLevel:9}).toFile(path.join(output,'png',p.id+'.png'));
      await sharp(Buffer.from(svg),{limitInputPixels:false}).resize({width:p.id==='cover'?1280:p.id==='social'?1086:809}).png().toFile(path.join(preview,p.id+'-preview.png'));
    }
  }
  exports.push({id:p.id,label:p.label,png:`07-final/png/${p.id}.png`,outlinedSVG:`07-final/svg-outlined/${p.id}.svg`,editableSVG:`07-final/svg-editable/${p.id}.svg`,width:p.ew,height:p.eh});
  console.log('Exported '+p.id+' '+p.ew+'x'+p.eh);
}
await fs.writeFile(path.join(here,'layout-manifest.json'),JSON.stringify(pages.map(({id,w,h,alignment,elements})=>({id,width:w,height:h,alignment,elements})),null,2));
await fs.writeFile(path.join(here,'rendered-text-bounds.json'),JSON.stringify(usedBounds,null,2));
await fs.writeFile(path.join(output,'manifest.json'),JSON.stringify(exports,null,2));
console.log('Completed '+exports.length+' designs.');
