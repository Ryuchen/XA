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
function runs(text){return String(text).split(/(\*\*.*?\*\*)/).filter(Boolean).map(t=>({text:t.startsWith('**')?t.slice(2,-2):t,bold:t.startsWith('**')}));}
function wrap(text,width,size=46){
 const out=[];let line=[],advance=0;
 for(const r of runs(text)){
  const tokens=r.text.match(/无赔付|大师以下|大师以上|非BAN必选|净胜|赔付|结算|王者|英雄|辅助|账号|段位|当局|客服|游戏|免费|[≥≤＜＞＋+]?\d+(?:\.\d+)?(?:[–～~\-]\d+)?(?:%|分|次|把|局|r)?|[A-Za-z]+[A-Za-z0-9]*(?:[.\/%+–≥＜→\-][A-Za-z0-9]+)*|\s+|[^\x00-\x7F]|./gu)||[];
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
 const id=req(t,fonts.body,bold);elements.push({type:'text',id,text:t,x,cy,size,bold,anchor,fill,role,container,font:fonts.body});
}
function rich(rs,x,cy,size=46,anchor='start',role='rule-body',container){
 const clean=rs.filter(r=>r.text);for(const r of clean)req(r.text,fonts.body,r.bold);
 elements.push({type:'rich',runs:clean.map(r=>({...r,id:req(r.text,fonts.body,r.bold),size,font:fonts.body,fill:navy})),x,cy,size,anchor,role,container});
}
function art(t,x,cy,size,maxWidth,anchor='middle',container){
 const ids=[...t].map(ch=>req(ch,fonts.art));elements.push({type:'art',text:t,ids,x,cy,size,maxWidth,anchor,container,fill:navy,outline:'#fffaf0',shadow:page.accent,outlineWidth:7});
}
function priceElement(pr,x,cy,size=66,container){
 if(pr.amount==='—'){text('—',x,cy,46,false,'middle',muted,'no-price',container);return;}
 const parts=pr.amount.match(/^(.+?)(起)?$/);const number=parts[1],suffix=parts[2]||'';
 req(number,fonts.num,true);req(pr.unit,fonts.num,true);if(suffix)req(suffix,fonts.body,true);
 elements.push({type:'price',text:number+pr.unit+suffix,amount:number,unit:pr.unit,suffix,x,cy,size,container,fill:gold,role:'price'});
}
function markContainer(id,r){containers.push({id,...r});return id;}
function lineHeight(size=46){return size*1.37;}
function linesInto(lines,r,size=46,role='rule-body',anchor='start',container){
 const lh=lineHeight(size),height=(lines.length-1)*lh+size;
 const top=r.y+(r.h-height)/2;
 lines.forEach((rs,i)=>rich(rs,anchor==='middle'?r.x+r.w/2:r.x,top+size/2+i*lh,size,anchor,role,container));
}
function notesNeed(b,data){const lines=data.paragraphs.flatMap(t=>wrap(t,b.w-64,46));return {lines,h:Math.max(240,86+lines.length*lineHeight()+62)};}
function cardModels(b,data){
 const cols=b.columns||2,gap=24,cw=(b.w-gap*(cols-1))/cols;
 const models=data.cards.map(card=>{
  const title=wrap('**'+card.title+'**',cw-64,46),body=(card.body||'').split('\n').flatMap(t=>t?wrap(t,cw-64,44):[]);
  const total=title.length*58+(card.price?78+14:0)+(body.length?body.length*58+10:0);
  return {card,title,body,total};
 });
 return {models,cw,gap,cols,ch:Math.max((b.h-gap*(Math.ceil(b.cards/cols)-1))/Math.ceil(b.cards/cols),...models.map(m=>m.total+52))};
}
function populateBlock(b,data){
 const id=markContainer(page.id+'-'+b.id,{x:b.x,y:b.y,w:b.w,h:b.h,kind:b.kind});
 if(b.kind==='notes'){
  text(data.title,b.x+32,b.y+43,48,true,'start',navy,'block-heading',id);
  const lines=data.paragraphs.flatMap(t=>wrap(t,b.w-64,46));
  linesInto(lines,{x:b.x+32,y:b.y+106,w:b.w-64,h:b.h-130},46,'rule-body','start',id);return;
 }
 if(b.kind==='strip'||b.kind==='offer'){
  // Cover the faint central perforation in the approved offer ticket so it cannot cross copy.
  if(b.kind==='offer')elements.push({type:'markup',markup:`<rect x="${b.x+27}" y="${b.y+b.h/2-4}" width="${b.w-54}" height="8" fill="#f9f3e6"/>`});
  const lines=data.paragraphs.flatMap(t=>wrap(t,b.w-72,46));
  linesInto(lines,{x:b.x+36,y:b.y+28,w:b.w-72,h:b.h-56},46,'core-condition','middle',id);return;
 }
 if(b.kind==='cards'){
  const {models,cw,gap,cols}=cardModels(b,data),rows=Math.ceil(b.cards/cols),ch=(b.h-gap*(rows-1))/rows;
  models.forEach((m,i)=>{
   const x=b.x+i%cols*(cw+gap),y=b.y+Math.floor(i/cols)*(ch+gap),cid=markContainer(id+'-'+i,{x:x+24,y:y+24,w:cw-48,h:ch-48,kind:'card-copy'});
   let cy=y+(ch-m.total)/2+29;
   for(const rs of m.title){rich(rs,x+cw/2,cy,46,'middle','card-heading',cid);cy+=58;}
   if(m.card.price){cy+=30;priceElement(m.card.price,x+cw*.61,cy,72,cid);cy+=62;}
   if(m.body.length){cy+=10;for(const rs of m.body){rich(rs,x+cw/2,cy,44,'middle','card-body',cid);cy+=58;}}
  });return;
 }
 if(b.kind==='parallel-tables'){
  const cw=(b.w-24)/2,header=b.headerHeight||100;
  data.parallel.forEach((t,i)=>{
   const x=b.x+i*(cw+24);text(t.title+' · r/小时',x+cw/2,b.y+header/2,44,true,'middle','#fffaf0','table-heading',id);
   const rh=(b.h-header)/t.rows.length;
   t.rows.forEach((r,j)=>{const cy=b.y+header+rh*(j+.5);text(r[0],x+28,cy,44,false,'start',navy,'tier',id);priceElement(r[1],x+cw-66,cy,58,id);});
  });return;
 }
 if(b.kind==='table'){
  const widths=data.widths||Array(data.headers.length).fill(1/data.headers.length),header=b.headerHeight||80;
  let x=b.x;
  data.headers.forEach((h,i)=>{
   const cw=b.w*widths[i];const rs=wrap(h,cw-36,40);const lh=46;rs.forEach((r,j)=>{for(const v of r)v.bold=true;rich(r,x+cw/2,b.y+header/2+(j-(rs.length-1)/2)*lh,40,'middle','table-heading',id);elements.at(-1).runs.forEach(v=>v.fill='#fffaf0');});x+=cw;
  });
  const rh=(b.h-header)/data.rows.length;
  data.rows.forEach((row,j)=>{
   let x=b.x;const cy=b.y+header+(j+.5)*rh;
   row.forEach((cell,i)=>{
    const cw=b.w*widths[i];
    if(typeof cell==='string'){
     const rs=wrap(cell,cw-54,46);linesInto(rs,{x:x+27,y:cy-rh/2+12,w:cw-54,h:rh-24},46,'table-label',i===0?'start':'middle',id);
    }else if(cell.price){priceElement(cell.price,x+cw*.58,cy-28,64,id);text(cell.body,x+cw/2,cy+37,42,false,'middle',muted,'guarantee',id);}
    else priceElement(cell,x+cw*.62,cy,66,id);
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
   const core=page.coverCore.flatMap(t=>wrap(t,z.core.w-20,46));linesInto(core,z.core,46,'cover-core','middle',page.id+'-core');
   text(d.coverPlatform,429,z.version.y+z.version.h/2,44,true,'middle',navy,'platform',page.id+'-version');
  }else{
   const h=page.header;Object.entries(h).filter(([k])=>k!=='heroBox').forEach(([k,r])=>markContainer(page.id+'-'+k,r));
   text('兴安电竞',64,82,48,true,'start',navy,'brand',page.id+'-brandSafe');
   const ts=d.title;const multiSize=page.type==='advert'?66:74,multiGap=page.type==='advert'?76:86;
   ts.forEach((t,i)=>art(t,354,h.titleSafe.y+h.titleSafe.h/2+(i-(ts.length-1)/2)*multiGap,ts.length>1?multiSize:104,558,'middle',page.id+'-titleSafe'));
   text(d.subtitle,64,h.subtitleSafe.y+h.subtitleSafe.h/2,34,false,'start',muted,'platform',page.id+'-subtitleSafe');
   let y=page.blocks[0]?.y;
   for(const b of page.blocks){
    const data=(page.type==='advert'?d.advert?.[b.id]:null)||d.blocks[b.id];if(!data)throw Error('Missing copy '+page.id+'/'+b.id);
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
plan.revision='final-r1';plan.stage='final-typeset';
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
  const top=Math.min((num.y-num.ascent)*ns,(unit.y-unit.ascent)*us),bottom=Math.max((num.y+num.height-num.ascent)*ns,(unit.y+unit.height-unit.ascent)*us);
  const base=e.cy-(top+bottom)/2;
  let markup=textNode({id:req(e.amount,fonts.num,true),text:e.amount,size:e.size,x:e.x,anchor:'end',fill:e.fill,role:'price-number',container:e.container},editable,base);
  markup+=textNode({id:req(e.unit,fonts.num,true),text:e.unit,size:unitSize,x:e.x+gap,fill:e.fill,role:'price-unit',container:e.container},editable,base);
  if(e.suffix)markup+=textNode({id:req(e.suffix,fonts.body,true),text:e.suffix,size:42,x:e.x+gap+unit.width*us+9,fill:e.fill,role:'price-suffix',container:e.container},editable,base);
  return markup;
 }
 function artNode(e,editable){
  let chars=[],cursor=0;const natural=e.ids.reduce((n,id)=>n+cache[id].width/100,0)+.065*(e.ids.length-1);
  const size=Math.min(e.size,(e.maxWidth-20)/natural),s=size/100,angles=[-2,1.2,-1.5,1.6,-.8];
  for(let i=0;i<e.ids.length;i++){const g=cache[e.ids[i]],w=g.width*s,h=g.height*s;chars.push({g,w,h,x:cursor+w/2,angle:angles[i%angles.length]});cursor+=w+size*.065;}
  const width=cursor-size*.065,shift=e.x-width/2;
  const result=chars.map(v=>{const d=`<path d="${v.g.d}" transform="translate(${fmt(-v.g.x*s-v.w/2)} ${fmt(-(v.g.y+v.g.height/2)*s)}) scale(${fmt(s)})"/>`;return `<g transform="translate(${fmt(shift+v.x)} ${fmt(e.cy)}) rotate(${v.angle})"><g transform="translate(3 5)" fill="${e.shadow}">${d}</g><g fill="${e.fill}" stroke="${e.outline}" stroke-width="${fmt(e.outlineWidth/s)}" stroke-linejoin="round" paint-order="stroke fill">${d}</g></g>`;}).join('');
  if(!editable)boundsPush({...e,role:'art-title'},shift-6,e.cy-Math.max(...chars.map(v=>v.h))/2-12,width+12,Math.max(...chars.map(v=>v.h))+24,undefined);
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
