import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {execFileSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';
const root=path.resolve(import.meta.dirname,'..');
const modules=process.argv[2];
const sharp=createRequire(import.meta.url)(modules+'/sharp');
const c=JSON.parse(fs.readFileSync(root+'/content.json','utf8'));
const original=JSON.parse(fs.readFileSync(root+'/layout-plan.json','utf8'));
const state=JSON.parse(fs.readFileSync(root+'/campaign-state.json','utf8'));
if(!state.contentVerified||state.approvals.backgrounds.status!=='approved')throw Error('Content and background approval required.');
const hash=s=>createHash('sha256').update(s).digest('hex');
const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const fmt=n=>Number(n.toFixed(3));
const read=f=>fs.readFileSync(root+'/'+f);
const write=(f,data)=>{fs.mkdirSync(path.dirname(root+'/'+f),{recursive:true});fs.writeFileSync(root+'/'+f,data);};
let cache={};try{cache=JSON.parse(read('.cache/font-outlines.json'));}catch{}
const requests=new Map();
const fonts={body:'Microsoft YaHei UI',num:'Bahnschrift',art:'Noto Sans SC Black'};
const navy='#17334b',muted='#476c86',gold='#a77735';
const inset=36,revision='final-r2';
function displayText(t){let s=String(t).replaceAll('–','-').replace(/(\d+)分-(\d+)分/g,'$1-$2分');if((s.match(/·/g)||[]).length>1)s=s.replaceAll(' · ',' / ');return s;}
function normalizeDisplay(v){if(typeof v==='string')return displayText(v);if(Array.isArray(v))return v.map(normalizeDisplay);if(v&&typeof v==='object')return Object.fromEntries(Object.entries(v).map(([k,x])=>[k,normalizeDisplay(x)]));return v;}
for(const set of c.sets)set.display=normalizeDisplay(set.display);
function req(text,font=fonts.body,bold=false){
 const id=hash(JSON.stringify([text,font,bold?1:0]));
 requests.set(id,{id,text,font,style:bold?1:0});return id;
}
function glyph(text,font=fonts.body,bold=false){return cache[req(text,font,bold)];}
function measure(text,size,font=fonts.body,bold=false){
 const g=glyph(text,font,bold);if(g)return g.advance*size/100;
 return [...text].reduce((n,ch)=>n+(glyph(ch,font,bold)?.advance??100)*size/100,0);
}
function outline(){
 const missing=[...requests.values()].filter(r=>!cache[r.id]);if(!missing.length)return;
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'xa-lol-font-'));
 const input=temp+'/requests.json',output=temp+'/outlines.json';fs.writeFileSync(input,JSON.stringify(missing));
 console.log(execFileSync('powershell.exe',['-NoProfile','-ExecutionPolicy','Bypass','-File','C:/Users/chenh/.codex/skills/xingan-gameplay-posters/scripts/outline-text.ps1','-InputFile',input,'-OutputFile',output],{encoding:'utf8',maxBuffer:1024*1024}).trim());
 Object.assign(cache,JSON.parse(fs.readFileSync(output,'utf8')));write('.cache/font-outlines.json',JSON.stringify(cache));
 // Only remove the two explicitly created temporary files.
 fs.unlinkSync(input);fs.unlinkSync(output);fs.rmdirSync(temp);
}
function runs(text){return displayText(text).split(/(\*\*.*?\*\*)/).filter(Boolean).map(t=>({text:t.startsWith('**')?t.slice(2,-2):t,bold:t.startsWith('**')}));}
function wrap(text,width,size=46){
 const out=[];let line=[],advance=0;
 for(const r of runs(text)){
  const tokens=r.text.match(/下路ADC除外|ADC除外|AP英雄|无赔付|大师以下|大师以上|非BAN必选|物理英雄|物理|韩服大师|其他区千分|除外|宗师|大师|千分|净胜|赔付|结算|王者|英雄|辅助|账号|段位|当局|客服|游戏|免费|[≥≤＜＞＋+]?\d+(?:\.\d+)?(?:[–～~\-]\d+)?(?:净胜|胜率|%|分|次|把|局|r)?|[A-Za-z]+[A-Za-z0-9]*(?:[.\/%+–≥＜→\-][A-Za-z0-9]+)*|\s+|[^\x00-\x7F]|./gu)||[];
  for(const token of tokens){
   const tw=measure(token,size,fonts.body,r.bold);
   const closing=/^[，。；：、）】！？%]$/.test(token);
   if(advance+tw>width-16&&line.length&&!closing){out.push(line);line=[];advance=0;}
   if(!line.length&&/^\s+$/.test(token))continue;
   const last=line.at(-1);if(last&&last.bold===r.bold)last.text+=token;else line.push({text:token,bold:r.bold});advance+=tw;
  }
 }
 if(line.length)out.push(line);
 return out.map(rs=>rs.map((r,i)=>({...r,text:i===rs.length-1?r.text.trimEnd():r.text}))).filter(rs=>rs.some(r=>r.text));
}
// Measure characters first, then exact wrapped strings. Font geometry stays cached for later changes.
const all=JSON.stringify(c.sets.map(s=>s.display))+JSON.stringify(original.pages.map(p=>[p.titleLines,p.coverCore]))+'兴安电竞每小时完整价目英雄联盟端游r/分';
for(const ch of new Set([...all]))for(const [font,bold] of [[fonts.body,false],[fonts.body,true],[fonts.num,true],[fonts.art,false]])req(ch,font,bold);
outline();
let elements=[],containers=[],page;
function text(t,x,cy,size=46,bold=false,anchor='start',fill=navy,role='copy',container){
 t=displayText(t);
 const id=req(t,fonts.body,bold);elements.push({type:'text',id,text:t,x,cy,size,bold,anchor,fill,role,container,font:fonts.body});
}
function rich(rs,x,cy,size=46,anchor='start',role='rule-body',container){
 const clean=rs.filter(r=>r.text);for(const r of clean)req(r.text,fonts.body,r.bold);
 elements.push({type:'rich',runs:clean.map(r=>({...r,id:req(r.text,fonts.body,r.bold),size,font:fonts.body,fill:navy})),x,cy,size,anchor,role,container});
}
function art(t,x,cy,size,maxWidth,anchor='middle',container){
 t=displayText(t);
 const ids=[...t].map(ch=>req(ch,fonts.art));elements.push({type:'art',text:t,ids,x,cy,size,maxWidth,anchor,container,fill:navy,outline:'#fffaf0',shadow:page.accent,outlineWidth:7});
}
function priceElement(pr,x,cy,size=66,container,alignment='column'){
 if(pr.amount==='—'){text('未列价',x,cy,40,false,'middle',muted,'no-price',container);return;}
 const parts=pr.amount.match(/^(.+?)(起)?$/);const number=parts[1],suffix=parts[2]||'';
 req(number,fonts.num,true);req(pr.unit,fonts.num,true);if(suffix)req(suffix,fonts.body,true);
 elements.push({type:'price',text:number+pr.unit+suffix,amount:number,unit:pr.unit,suffix,x,cy,size,container,alignment,fill:gold,role:'price'});
}
function markContainer(id,r){containers.push({id,...r});return id;}
function lineHeight(size=46){return size*1.37;}
function linesInto(lines,r,size=46,role='rule-body',anchor='start',container){
 const lh=lineHeight(size),height=(lines.length-1)*lh+size;
 const top=r.y+(r.h-height)/2;
 lines.forEach((rs,i)=>rich(rs,anchor==='middle'?r.x+r.w/2:r.x,top+size/2+i*lh,size,anchor,role,container));
}
function geometry(t,size=46,font=fonts.body,bold=false){const g=glyph(t,font,bold);if(g)return {width:g.width*size/100,advance:g.advance*size/100,top:(g.y-g.ascent)*size/100,bottom:(g.y+g.height-g.ascent)*size/100,h:g.height*size/100};const w=measure(t,size,font,bold);return {width:w,advance:w,top:-size*.85,bottom:size*.12,h:size*.97};}
function richGeometry(rs,size){let cursor=0,previous;const pieces=[];for(const r of rs.filter(r=>r.text)){const g=glyph(r.text,fonts.body,r.bold),b=geometry(r.text,size,fonts.body,r.bold),inkOffset=(g?.x||0)*size/100;if(previous)cursor=Math.max(cursor,previous.x+previous.b.width+2-inkOffset);const part={x:cursor+inkOffset,b};pieces.push(part);previous=part;cursor+=b.advance;}const left=Math.min(...pieces.map(v=>v.x)),right=Math.max(...pieces.map(v=>v.x+v.b.width)),top=Math.min(...pieces.map(v=>v.b.top)),bottom=Math.max(...pieces.map(v=>v.b.bottom));return {width:right-left,top,bottom,h:bottom-top};}
function priceGeometry(pr,size=66){if(pr.amount==='—')return geometry('未列价',40);const parts=pr.amount.match(/^(.+?)(起)?$/),num=geometry(parts[1],size,fonts.num,true),unit=geometry(pr.unit,size*.72,fonts.num,true),suffix=parts[2]?geometry(parts[2],42,fonts.body,true):null;const top=Math.min(num.top,unit.top,suffix?.top??Infinity),bottom=Math.max(num.bottom,unit.bottom,suffix?.bottom??-Infinity);return {numWidth:num.width,tailWidth:8+unit.width+(suffix?9+suffix.width:0),width:num.width+8+unit.width+(suffix?9+suffix.width:0),h:bottom-top,top,bottom};}
function numericColumn(rows,index,size,center){const values=rows.map(r=>r[index]?.price||r[index]).filter(v=>v&&typeof v==='object'&&v.amount!=='—').map(v=>priceGeometry(v,size));const maxNumber=Math.max(0,...values.map(v=>v.numWidth)),maxTail=Math.max(0,...values.map(v=>v.tailWidth));return {end:center+(maxNumber-maxTail)/2,center,maxNumber,maxTail};}
function notesModel(b,data){const groups=data.paragraphs.map(t=>wrap(t,b.w-2*inset,46));const lineGap=17,paraGap=20;const groupHeights=groups.map(rs=>rs.reduce((n,r)=>n+richGeometry(r,46).h,0)+Math.max(0,rs.length-1)*lineGap);const height=groupHeights.reduce((a,b)=>a+b,0)+Math.max(0,groups.length-1)*paraGap;return {groups,groupHeights,lineGap,paraGap,height,h:Math.ceil(86+height+68)};}
function notesNeed(b,data){const model=notesModel(b,data);return {...model,h:Math.max(240,model.h)};}
function cardModels(b,data){
 const cols=b.columns||2,gap=24,cw=(b.w-gap*(cols-1))/cols;
 const models=data.cards.map(card=>{
  let cardTitle=card.title,bodyText=card.body||'';
  if(b.id==='qualification'&&b.cards===4)bodyText=bodyText.replaceAll(' / ','\n');
  if(page.setId==='01-solo-duo'&&page.type==='advert'&&b.id==='tiers'){
   const i=data.cards.indexOf(card);bodyText=c.sets.find(s=>s.id===page.setId).display.blocks.qualification.cards[i].body.replaceAll(' / ','\n');
   if(i===2)cardTitle='T2 / T2Pro · 招牌';
  }
  if(page.setId==='07-day'&&page.type==='advert'&&card.price){const parts=bodyText.split(/ \/ | · /);if(parts.length>1)bodyText=parts.slice(0,-1).join(' / ')+'\n'+parts.at(-1);}
  const title=wrap('**'+cardTitle+'**',cw-2*inset,46),body=bodyText.split('\n').flatMap(t=>t?wrap(t,cw-2*inset,44):[]);
  const items=[];for(const [i,rs] of title.entries())items.push({kind:'rich',rs,size:46,role:'card-heading',h:richGeometry(rs,46).h,gap:i?12:0});
  if(card.price)items.push({kind:'price',price:card.price,size:76,h:priceGeometry(card.price,76).h,gap:22});
  for(const [i,rs] of body.entries())items.push({kind:'rich',rs,size:44,role:'card-body',h:richGeometry(rs,44).h,gap:i?14:card.price?24:18});
  const total=items.reduce((n,v)=>n+v.h+v.gap,0);
  return {card,title,body,items,total};
 });
 return {models,cw,gap,cols,ch:Math.max((b.h-gap*(Math.ceil(b.cards/cols)-1))/Math.ceil(b.cards/cols),...models.map(m=>m.total+68))};
}
function populateBlock(b,data){
 const id=markContainer(page.id+'-'+b.id,{x:b.x,y:b.y,w:b.w,h:b.h,kind:b.kind});
 if(b.kind==='notes'){
  text(data.title,b.x+inset,b.y+43,48,true,'start',navy,'block-heading',id);
  const model=notesModel(b,data);let top=b.y+86+(b.h-86-model.height)/2;
  model.groups.forEach((lines,i)=>{for(const [j,rs]of lines.entries()){const h=richGeometry(rs,46).h;rich(rs,b.x+inset,top+h/2,46,'start','rule-body',id);top+=h+(j<lines.length-1?model.lineGap:0);}if(i<model.groups.length-1)top+=model.paraGap;});return;
 }
 if(b.kind==='strip'||b.kind==='offer'){
  // Cover the faint central perforation in the approved offer ticket so it cannot cross copy.
  if(b.kind==='offer')elements.push({type:'markup',markup:`<rect x="${b.x+27}" y="${b.y+b.h/2-4}" width="${b.w-54}" height="8" fill="#f9f3e6"/>`});
  const lines=data.paragraphs.flatMap(t=>wrap(t,b.w-2*inset,46));
  linesInto(lines,{x:b.x+inset,y:b.y+28,w:b.w-2*inset,h:b.h-56},46,'core-condition',b.kind==='offer'?'middle':'start',id);return;
 }
 if(b.kind==='cards'){
  const {models,cw,gap,cols}=cardModels(b,data),rows=Math.ceil(b.cards/cols),ch=(b.h-gap*(rows-1))/rows;
  models.forEach((m,i)=>{
   const x=b.x+i%cols*(cw+gap),y=b.y+Math.floor(i/cols)*(ch+gap),cid=markContainer(id+'-'+i,{x:x+24,y:y+24,w:cw-48,h:ch-48,kind:'card-copy'});
   let top=y+(ch-m.total)/2;const anchor=m.card.price?'middle':'start',tx=m.card.price?x+cw/2:x+inset;
   for(const item of m.items){top+=item.gap;const cy=top+item.h/2;if(item.kind==='price')priceElement(item.price,x+cw/2,cy,item.size,cid,'middle');else rich(item.rs,tx,cy,item.size,anchor,item.role,cid);top+=item.h;}
  });return;
 }
 if(b.kind==='parallel-tables'){
  const cw=(b.w-24)/2,header=b.headerHeight||100;
  data.parallel.forEach((t,i)=>{
   const x=b.x+i*(cw+24),column=numericColumn(t.rows,1,58,x+cw-108);
   text(t.title,x+inset,b.y+header/2,44,true,'start','#fffaf0','table-heading',id);
   text('r/小时',column.center,b.y+header/2,36,true,'middle','#fffaf0','table-unit-heading',id);
   const rh=(b.h-header)/t.rows.length;
   t.rows.forEach((r,j)=>{const cy=b.y+header+rh*(j+.5);text(r[0],x+inset,cy,44,false,'start',navy,'tier',id);priceElement(r[1],column.end,cy,58,id);});
  });return;
 }
 if(b.kind==='table'){
  const widths=data.widths||Array(data.headers.length).fill(1/data.headers.length),header=b.headerHeight||80;
  let x=b.x;const starts=widths.map((v,i)=>b.x+b.w*widths.slice(0,i).reduce((a,z)=>a+z,0));
  const numeric=widths.map((v,i)=>numericColumn(data.rows,i,data.rows.some(r=>r[i]?.price)?64:66,starts[i]+b.w*v/2));
  data.headers.forEach((h,i)=>{
   const cw=b.w*widths[i];const rs=wrap(h,cw-2*inset,40);const lh=46;rs.forEach((r,j)=>{for(const v of r)v.bold=true;rich(r,i===0?x+inset:x+cw/2,b.y+header/2+(j-(rs.length-1)/2)*lh,40,i===0?'start':'middle','table-heading',id);elements.at(-1).runs.forEach(v=>v.fill='#fffaf0');});x+=cw;
  });
  const rh=(b.h-header)/data.rows.length;
  data.rows.forEach((row,j)=>{
   let x=b.x;const cy=b.y+header+(j+.5)*rh;
   row.forEach((cell,i)=>{
    const cw=b.w*widths[i];
    if(typeof cell==='string'){
     const rs=wrap(cell,cw-2*inset,46);linesInto(rs,{x:x+inset,y:cy-rh/2+12,w:cw-2*inset,h:rh-24},46,'table-label',i===0?'start':'middle',id);
    }else if(cell.price){priceElement(cell.price,numeric[i].end,cy-26,64,id);text(cell.body,x+cw/2,cy+38,44,false,'middle',muted,'guarantee',id);}
    else if(cell.amount==='—')text('未列价',x+cw/2,cy,40,false,'middle',muted,'no-price',id);
    else priceElement(cell,numeric[i].end,cy,66,id);
    x+=cw;
   });
  });
 }
}
function build(){
 const plan=structuredClone(original);let result=[];
 for(page of plan.pages){
  elements=[];containers=[];const set=c.sets.find(s=>s.id===page.setId),d=set.display;
  if(page.type==='cover'){
   for(const [k,r] of Object.entries(page.copyZones))markContainer(page.id+'-'+k,r);
   const z=page.copyZones;
   text('兴安电竞',z.brand.x+z.brand.w/2,z.brand.y+z.brand.h/2,54,true,'middle',navy,'brand',page.id+'-brand');
   const ts=page.titleLines;ts.forEach((t,i)=>art(t,429,z.title.y+z.title.h/2+(i-(ts.length-1)/2)*115,ts.length>1?92:125,680,'middle',page.id+'-title'));
   z.core.h=192;const core=page.coverCore.flatMap((t,i)=>{const source=displayText(t),size=i===0?50:46,parts=i>0&&source.includes(' · ')&&measure(source,size)>z.core.w-64?source.split(' · '):[source];return parts.flatMap(part=>wrap(i===0||part.includes('折')?'**'+part+'**':part,z.core.w-48,size).map(rs=>({rs,size})));});const heights=core.map(v=>richGeometry(v.rs,v.size).h),total=heights.reduce((a,z)=>a+z,0)+(core.length-1)*18;let y=z.core.y+(z.core.h-total)/2;core.forEach((v,i)=>{rich(v.rs,429,y+heights[i]/2,v.size,'middle','cover-core',page.id+'-core');y+=heights[i]+18;});
   const cr=containers.find(r=>r.id===page.id+'-core');cr.h=z.core.h;
   text(d.coverPlatform,429,z.version.y+z.version.h/2,44,true,'middle',navy,'platform',page.id+'-version');
  }else{
   const h=page.header;Object.entries(h).filter(([k])=>k!=='heroBox').forEach(([k,r])=>markContainer(page.id+'-'+k,r));
   text('兴安电竞',64,82,48,true,'start',navy,'brand',page.id+'-brandSafe');
   const ts=d.title;const multiSize=page.type==='advert'?66:74,multiGap=page.type==='advert'?76:86;
   ts.forEach((t,i)=>art(t,64,h.titleSafe.y+h.titleSafe.h/2+(i-(ts.length-1)/2)*multiGap,ts.length>1?multiSize:104,558,'start',page.id+'-titleSafe'));
   text(d.subtitle,64,h.subtitleSafe.y+h.subtitleSafe.h/2,34,false,'start',muted,'platform',page.id+'-subtitleSafe');
   let y=page.blocks[0]?.y;
   for(const b of page.blocks){
    const data=(page.type==='advert'?d.advert?.[b.id]:null)||d.blocks[b.id];if(!data)throw Error('Missing copy '+page.id+'/'+b.id);
    if(b.kind==='table'){const old=b.headerHeight||80,maxLines=Math.max(...data.headers.map((s,i)=>wrap(s,b.w*(data.widths?.[i]||1/data.headers.length)-2*inset,40).length));const header=Math.max(old,maxLines*46+32);b.headerHeight=header;b.h+=header-old;}
    if(b.kind==='cards'){const m=cardModels(b,data);b.h=Math.ceil(m.ch*Math.ceil(b.cards/m.cols)+m.gap*(Math.ceil(b.cards/m.cols)-1));}
    if(page.type==='price'){
     if(b.kind==='notes')b.h=Math.ceil(notesNeed(b,data).h);
    }
    b.y=y;populateBlock(b,data);y+=b.h+28;
   }
   if(page.type==='price'){page.h=y+152;page.eh=Math.round(page.h*page.ew/page.w);page.footer={x:0,y:page.h-170,w:page.w,h:170};}
  }
  page.elements=elements;page.containers=containers;result.push(page);
 }
 plan.pages=result;return plan;
}
let plan=build();outline();plan=build();outline();plan=build();
plan.revision=revision;plan.stage='final-typeset';
write('layout-final-plan.json',JSON.stringify({...plan,pages:plan.pages.map(({elements,containers,...p})=>p)},null,2));
write('layout.json',JSON.stringify({...plan,requests:[...requests.values()]},null,2));
if(process.argv.includes('--layout-only')){console.log('Built '+plan.pages.length+' layouts.');process.exit(0);}
// Rebuild only pages whose containers grew; approved art and original covers/adverts are reused unchanged.
const changed=plan.pages.filter(p=>{const o=original.pages.find(o=>o.id===p.id);return p.h!==o.h||JSON.stringify(p.blocks)!==JSON.stringify(o.blocks);});
if(changed.length){
 let script=fs.readFileSync(root+'/source/render-backgrounds.mjs','utf8');
 script=script.slice(0,script.indexOf('async function board'));
 script=script.replace("'layout-plan.json'","'layout-final-plan.json'");
 script=script.replace('for(const a of environment.assets){fs.copyFileSync(a.sourceFile,path.join(project,a.file));a.native=await sharp(read(a.file)).metadata().then(m=>({width:m.width,height:m.height}));}', 'for(const a of environment.assets){a.native=await sharp(read(a.file)).metadata().then(m=>({width:m.width,height:m.height}));}');
 script=script.replace('for(const p of plan.pages){','for(const p of plan.pages.filter(p=>'+JSON.stringify(changed.map(p=>p.id))+'.includes(p.id))){');
 script=script.replaceAll('bg-r1','bg-r2').replaceAll('guide-r1','guide-r2').replaceAll('checks-r1','checks-r2');
 write('source/render-final-backgrounds.mjs',script);
 console.log(execFileSync(process.execPath,[root+'/source/render-final-backgrounds.mjs',modules],{encoding:'utf8',maxBuffer:2*1024*1024}));
}
const check=[],manifest=[];
for(const p of plan.pages){
 if(process.argv.includes('--only')&&!process.argv[process.argv.indexOf('--only')+1].split(',').includes(p.id))continue;
 let bounds=[];
 function boundsPush(e,x,y,w,h,base){bounds.push({text:e.text||e.runs?.map(r=>r.text).join(''),role:e.role,container:e.container,x,y,w,h,baseline:base,size:e.size});}
 function textNode(e,editable,baseline,dx){
  const g=cache[e.id],s=e.size/100,w=g.width*s,h=g.height*s;
  const x=dx??(e.x-(e.anchor==='middle'?w/2:e.anchor==='end'?w:0));
  const base=baseline??e.cy-(g.y-g.ascent+g.height/2)*s;
  const y=base+(g.y-g.ascent)*s;
  if(!editable)boundsPush(e,x,y,w,h,base);
  const tx=x-g.x*s,ty=base-g.ascent*s;
  const node=editable?`<text x="0" y="${fmt(g.ascent)}" font-family="${esc(g.font)}" font-size="100" font-weight="${g.style===1?700:400}">${esc(e.text)}</text>`:`<path d="${g.d}"/>`;
  return `<g data-text="${esc(e.text)}" transform="translate(${fmt(tx)} ${fmt(ty)}) scale(${fmt(s)})" fill="${e.fill}">${node}</g>`;
 }
 function richNode(e,editable){
  let cursor=0,rs=[];
  for(const r of e.runs){const g=cache[r.id],s=r.size/100;const prev=rs.at(-1);if(prev)cursor=Math.max(cursor,prev.x+prev.g.width*prev.s+2-g.x*s);rs.push({r,g,s,x:cursor+g.x*s,top:(g.y-g.ascent)*s,bottom:(g.y+g.height-g.ascent)*s});cursor+=g.advance*s;}
  const left=Math.min(...rs.map(v=>v.x)),right=Math.max(...rs.map(v=>v.x+v.g.width*v.s));
  const shift=e.x-(e.anchor==='middle'?(left+right)/2:left),base=e.cy-(Math.min(...rs.map(v=>v.top))+Math.max(...rs.map(v=>v.bottom)))/2;
  return rs.map(v=>textNode({...v.r,text:v.r.text,role:e.role,container:e.container,x:0,size:v.r.size},editable,base,shift+v.x)).join('');
 }
 function priceNode(e,editable){
  const unitSize=e.size*.72,num=cache[req(e.amount,fonts.num,true)],unit=cache[req(e.unit,fonts.num,true)];
  const ns=e.size/100,us=unitSize/100,gap=8;
  const suffix=e.suffix?cache[req(e.suffix,fonts.body,true)]:null,ss=42/100;
  const top=Math.min((num.y-num.ascent)*ns,(unit.y-unit.ascent)*us,suffix?(suffix.y-suffix.ascent)*ss:Infinity),bottom=Math.max((num.y+num.height-num.ascent)*ns,(unit.y+unit.height-unit.ascent)*us,suffix?(suffix.y+suffix.height-suffix.ascent)*ss:-Infinity);
  const base=e.cy-(top+bottom)/2;
  const total=num.width*ns+gap+unit.width*us+(suffix?9+suffix.width*ss:0),end=e.alignment==='middle'?e.x-total/2+num.width*ns:e.x;
  let markup=textNode({id:req(e.amount,fonts.num,true),text:e.amount,size:e.size,x:end,anchor:'end',fill:e.fill,role:'price-number',container:e.container},editable,base);
  markup+=textNode({id:req(e.unit,fonts.num,true),text:e.unit,size:unitSize,x:end+gap,fill:e.fill,role:'price-unit',container:e.container},editable,base);
  if(e.suffix)markup+=textNode({id:req(e.suffix,fonts.body,true),text:e.suffix,size:42,x:end+gap+unit.width*us+9,fill:e.fill,role:'price-suffix',container:e.container},editable,base);
  return markup;
 }
 function artNode(e,editable){
  let chars=[],cursor=0;const natural=e.ids.reduce((n,id)=>n+cache[id].width/100,0)+.065*(e.ids.length-1);
  const size=Math.min(e.size,(e.maxWidth-20)/natural),s=size/100,angles=[-2,1.2,-1.5,1.6,-.8];
  for(let i=0;i<e.ids.length;i++){const g=cache[e.ids[i]],w=g.width*s,h=g.height*s;chars.push({g,w,h,x:cursor+w/2,angle:angles[i%angles.length]});cursor+=w+size*.065;}
  const corners=[];for(const v of chars){const a=v.angle*Math.PI/180,co=Math.cos(a),si=Math.sin(a);for(const [ox,oy,pad] of [[0,0,e.outlineWidth/2],[3,5,0]])for(const x of [-v.w/2-pad,v.w/2+pad])for(const y of [-v.h/2-pad,v.h/2+pad])corners.push([v.x+(x+ox)*co-(y+oy)*si,(x+ox)*si+(y+oy)*co]);}
  const l=Math.min(...corners.map(v=>v[0])),r=Math.max(...corners.map(v=>v[0])),t=Math.min(...corners.map(v=>v[1])),b=Math.max(...corners.map(v=>v[1]));
  const shift=e.x-(e.anchor==='start'?l:(l+r)/2),vertical=e.cy-(t+b)/2;
  const result=chars.map(v=>{const d=`<path d="${v.g.d}" transform="translate(${fmt(-v.g.x*s-v.w/2)} ${fmt(-(v.g.y+v.g.height/2)*s)}) scale(${fmt(s)})"/>`;return `<g transform="translate(${fmt(shift+v.x)} ${fmt(vertical)}) rotate(${v.angle})"><g transform="translate(3 5)" fill="${e.shadow}">${d}</g><g fill="${e.fill}" stroke="${e.outline}" stroke-width="${fmt(e.outlineWidth/s)}" stroke-linejoin="round" paint-order="stroke fill">${d}</g></g>`;}).join('');
  if(!editable)boundsPush({...e,role:'art-title'},shift+l,vertical+t,r-l,b-t,undefined);
  return result;
 }
 const bg=read('assets/backgrounds/'+p.id+'-'+(changed.some(v=>v.id===p.id)?'bg-r2':'bg-r1')+'.png');
 const head=`<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="${p.ew}" height="${p.eh}" viewBox="0 0 ${p.w} ${p.h}"><title>${esc(p.label)}</title><desc>兴安电竞英雄联盟；标题与正文、价格为矢量，手绘人物与环境为内嵌位图。</desc><image width="${p.w}" height="${p.h}" xlink:href="data:image/png;base64,${bg.toString('base64')}"/>`;
 for(const editable of [false,true]){
  const svg=head+p.elements.map(e=>e.type==='text'?textNode(e,editable):e.type==='rich'?richNode(e,editable):e.type==='price'?priceNode(e,editable):e.type==='art'?artNode(e,editable):e.markup).join('')+'</svg>';
  write('output/svg-'+(editable?'editable':'outlined')+'/'+p.id+'.svg',svg);
  if(!editable){const png=await sharp(Buffer.from(svg),{limitInputPixels:false}).withMetadata({density:300}).png({compressionLevel:8}).toBuffer();write('output/png/'+p.id+'.png',png);write('review/final/'+p.id+'-390.png',await sharp(png,{limitInputPixels:false}).resize({width:390}).png().toBuffer());}
 }
 const failures=[];
 const regular=bounds.filter(b=>b.role!=='art-title');
 for(const b of bounds){const r=p.containers.find(r=>r.id===b.container);if(!r){failures.push({type:'missing-container',text:b.text});continue;}const epsilon=1;if(b.x<r.x-epsilon||b.x+b.w>r.x+r.w+epsilon||b.y<r.y-epsilon||b.y+b.h>r.y+r.h+epsilon)failures.push({type:'container-overflow',text:b.text,box:b,container:r});}
 for(let i=0;i<regular.length;i++)for(let j=i+1;j<regular.length;j++){const a=regular[i],b=regular[j],ox=Math.min(a.x+a.w,b.x+b.w)-Math.max(a.x,b.x),oy=Math.min(a.y+a.h,b.y+b.h)-Math.max(a.y,b.y);if(ox>1&&oy>1)failures.push({type:'text-overlap',a:a.text,b:b.text,ox,oy});}
 check.push({id:p.id,failures});write('review/final/'+p.id+'-bounds.json',JSON.stringify(bounds,null,2));
 manifest.push({id:p.id,label:p.label,png:'output/png/'+p.id+'.png',editableSVG:'output/svg-editable/'+p.id+'.svg',outlinedSVG:'output/svg-outlined/'+p.id+'.svg',preview:'review/final/'+p.id+'-390.png',width:p.ew,height:p.eh,layoutWidth:p.w,layoutHeight:p.h,backgroundNativeWidth:p.w,backgroundNativeHeight:p.h});
 console.log('Exported '+p.id+' '+p.ew+'x'+p.eh+'; checks '+failures.length);
}
let oldChecks=[],oldManifest=[];try{oldChecks=JSON.parse(read('review/final/layout-checks.json'));oldManifest=JSON.parse(read('output/manifest.json'));}catch{}
const checkMap=new Map(oldChecks.map(x=>[x.id,x])),manifestMap=new Map(oldManifest.map(x=>[x.id,x]));check.forEach(x=>checkMap.set(x.id,x));manifest.forEach(x=>manifestMap.set(x.id,x));
write('review/final/layout-checks.json',JSON.stringify(plan.pages.map(p=>checkMap.get(p.id)),null,2));
write('output/manifest.json',JSON.stringify(plan.pages.map(p=>manifestMap.get(p.id)),null,2));
console.log(JSON.stringify({pages:manifest.length,failed:check.filter(r=>r.failures.length).map(r=>({id:r.id,failures:r.failures.length}))}));
