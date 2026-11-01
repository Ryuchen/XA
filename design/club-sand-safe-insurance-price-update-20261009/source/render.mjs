import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import {inlineMetrics} from './text-metrics.mjs';

const here=path.dirname(fileURLToPath(import.meta.url));
const hash=value=>createHash('sha256').update(value).digest('hex');
const esc=value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const fmt=n=>Number(n.toFixed(4));
const exists=async p=>fs.access(p).then(()=>true,()=>false);
const readJSON=async(p,fallback)=>fs.readFile(p,'utf8').then(s=>JSON.parse(s.replace(/^\uFEFF/,''))).catch(e=>{if(e.code==='ENOENT'&&fallback!==undefined)return fallback;throw e;});
const elementIds=e=>e.type==='text'?[e.id]:e.type==='art'?e.ids:e.type==='inline'?e.parts.map(p=>p.id):[];

export function loadSharp(modules){
  if(modules)return createRequire(import.meta.url)(path.join(path.resolve(modules),'sharp'));
  for(const base of [path.resolve(path.dirname(process.execPath),'..','runtime.cjs'),path.join(here,'runtime.cjs')]){
    try{return createRequire(base)('sharp');}catch{}
  }
  throw new Error('找不到 Sharp。使用 workspace dependency loader 提供的 Node，或传 --modules <Node包目录>。');
}

export async function renderProject(project,options={}){
  project=path.resolve(project);
  const state=await readJSON(path.join(project,'campaign-state.json'));
  if(state.contentVerified!==true)throw new Error('先依据本轮资料核对 content.json，并在 campaign-state.json 记录 contentVerified=true 及核对依据；历史样例不能直接当作新价格。');
  const plan=await readJSON(path.join(project,'layout.json'));
  const scope=state.deliveryScope;
  if(!scope||JSON.stringify([...scope.pageIds].sort())!==JSON.stringify(plan.pages.map(p=>p.id).sort()))throw Error('页面集合与本轮交付范围不一致');
  for(const p of plan.pages)for(const platform of p.platforms??[])if(!state.verifiedPricePlatforms.includes(platform)||!state.priceSourceByPlatform[platform])throw Error('平台缺少已核对来源：'+platform);
  const selection=options.only?options.only.split(',').filter(Boolean):plan.pages.map(p=>p.id);
  for(const id of selection)if(!plan.pages.some(p=>p.id===id))throw new Error('未知页面: '+id);
  const pages=plan.pages.filter(p=>selection.includes(p.id));
  for(const p of pages)if(!/^[a-zA-Z0-9_-]+$/.test(p.id))throw new Error('页面 id 只能包含字母、数字、下划线和短横线');
  const sharp=loadSharp(options.modules),cacheDir=path.join(project,'.cache');
  for(const dir of [cacheDir,'output/png','output/svg-outlined','output/svg-editable','review'].map(d=>path.isAbsolute(d)?d:path.join(project,d)))await fs.mkdir(dir,{recursive:true});
  const glyphFile=path.join(cacheDir,'font-outlines.json');
  const glyphCache=options.force?{}:await readJSON(glyphFile,{}),glyphs={},missing=[];
  const needed=new Set(pages.flatMap(p=>p.elements.flatMap(elementIds)));
  for(const request of plan.requests.filter(r=>needed.has(r.id))){
    const key=hash(JSON.stringify([request.text,request.font,request.style]));
    request.cacheKey=key;
    if(!glyphCache[key])missing.push({...request,id:key});
  }
  if(missing.length){
    if(process.platform!=='win32')throw new Error('当前转曲器使用 Windows System.Drawing；此环境需要替换字体轮廓工具或沿用已有转曲 SVG。');
    const input=path.join(cacheDir,'new-text.json'),output=path.join(cacheDir,'new-outlines.json');
    await fs.writeFile(input,JSON.stringify(missing));
    console.log(execFileSync('powershell.exe',['-NoProfile','-ExecutionPolicy','Bypass','-File',path.join(here,'outline-text.ps1'),'-InputFile',input,'-OutputFile',output],{encoding:'utf8'}).trim());
    Object.assign(glyphCache,await readJSON(output));
    await fs.writeFile(glyphFile,JSON.stringify(glyphCache));
  }
  for(const request of plan.requests.filter(r=>needed.has(r.id)))glyphs[request.id]=glyphCache[request.cacheKey];
  for(const id of needed)if(!glyphs[id])throw new Error('缺少文字请求: '+id);
  function textElement(e,editable,bounds){
    const g=glyphs[e.id];
    let scale=e.size/100;if(e.maxWidth)scale=Math.min(scale,e.maxWidth/g.width);
    const w=g.width*scale,h=g.height*scale;
    const x=e.x-(e.anchor==='middle'?w/2:e.anchor==='end'?w:0);
    const baseline=e.baseline??e.cy-h/2+(g.ascent-g.y)*scale;
    const y=baseline+(g.y-g.ascent)*scale;
    const dx=x-g.x*scale,dy=y-g.y*scale;
    if(!editable)bounds.push({text:e.text,role:e.role,x,y,width:w,height:h,cy:e.cy,baseline,lineGroup:e.lineGroup,effectiveSize:fmt(scale*100)});
    const stroke=e.stroke?` stroke="${e.stroke}" stroke-width="${fmt(e.strokeWidth/scale)}" stroke-linejoin="round" paint-order="stroke fill"`:'';
    const node=editable?`<text x="0" y="${fmt(g.ascent)}" font-family="${esc(g.font)}" font-size="100" font-weight="${g.style===1?'700':'400'}"${stroke}>${esc(e.text)}</text>`:`<path d="${g.d}"${stroke}/>`;
    return `<g data-text="${esc(e.text)}" data-role="${esc(e.role||'copy')}" transform="translate(${fmt(dx)} ${fmt(dy)}) scale(${fmt(scale)})" fill="${e.fill}">${node}</g>`;
  }
  function inlineElement(e,editable,bounds){
    const metrics=inlineMetrics(e.parts,glyphs,e.gap),scale=e.maxWidth?Math.min(1,e.maxWidth/metrics.width):1,total=metrics.width*scale;
    const left=e.x-(e.anchor==='middle'?total/2:e.anchor==='end'?total:0);
    const baseline=e.cy-(metrics.top+metrics.bottom)*scale/2;
    return '<g data-inline-group="'+esc(e.role)+'">'+metrics.entries.map(({part:p,left:offset})=>{
      return textElement({...p,x:left+(offset-metrics.left)*scale,cy:e.cy,baseline,lineGroup:e.role+'-'+e.cy,size:p.size*scale,anchor:'start'},editable,bounds);
    }).join('')+'</g>';
  }
  function artElement(e,bounds){
    if(bounds&&e.visibleBounds)bounds.push({text:e.text,role:e.role||'art-title',x:e.visibleBounds[0],y:e.visibleBounds[1],width:e.visibleBounds[2],height:e.visibleBounds[3],requestedCenter:e.requestedCenter,effectiveSize:e.size,includesOutlineAndShadow:true});
    let size=e.size;
    const natural=e.ids.reduce((sum,id)=>sum+glyphs[id].width/100,0)+.065*(e.ids.length-1);
    if(e.maxWidth)size=Math.min(size,e.maxWidth/natural);
    let x=e.x,s=`<g data-art-title="${esc(e.text)}" aria-label="${esc(e.text)}">`;
    const angles=[-2.8,1.4,-1.7,2.2,-1.4],bumps=[1,-2,1,-1,0];
    e.ids.forEach((id,i)=>{
      const g=glyphs[id],scale=size/100,w=g.width*scale,angle=e.angles?angles[i%5]:0,cy=e.cy+(e.angles?bumps[i%5]*size/70:0);
      const d=`<path d="${g.d}" transform="translate(${fmt(-g.x*scale-w/2)} ${fmt(-(g.y+g.height/2)*scale)}) scale(${fmt(scale)})"/>`;
      s+=`<g transform="translate(${fmt(x+w/2)} ${fmt(cy)}) rotate(${angle})"><g transform="translate(${fmt(size*.035)} ${fmt(size*.055)})" fill="${e.shadow}" stroke="#213F53" stroke-width="${fmt(.6/scale)}" stroke-linejoin="round">${d}</g><g fill="${e.fill}" stroke="${e.outline}" stroke-width="${fmt(e.outlineWidth/scale)}" stroke-linejoin="round" paint-order="stroke fill">${d}</g></g>`;
      x+=w+size*.065;
    });
    return s+'</g>';
  }
  const pageCache=await readJSON(path.join(cacheDir,'pages.json'),{});
  const previous=await readJSON(path.join(project,'output/manifest.json'),[]);
  const records=new Map(previous.filter(r=>plan.pages.some(p=>p.id===r.id)).map(r=>[r.id,r]));
  const rendererHash=hash(await fs.readFile(fileURLToPath(import.meta.url)));
  for(const p of pages){
    const numericGroups=new Map();
    for(const e of p.elements.filter(e=>e.type==='text'&&/^(price|loot)-(number|unit)$/.test(e.role??''))) {
      const key=e.role.split('-')[0]+'-'+e.cy;
      if(!numericGroups.has(key))numericGroups.set(key,[]);
      numericGroups.get(key).push(e);
    }
    for(const [lineGroup,es]of numericGroups) {
      const top=Math.min(...es.map(e=>(glyphs[e.id].y-glyphs[e.id].ascent)*e.size/100));
      const bottom=Math.max(...es.map(e=>(glyphs[e.id].y+glyphs[e.id].height-glyphs[e.id].ascent)*e.size/100));
      for(const e of es){e.baseline=e.cy-(top+bottom)/2;e.lineGroup=lineGroup;}
    }
    const bgRoot=path.resolve(project,'assets/backgrounds'),bgPath=path.resolve(bgRoot,p.bg);
    if(!bgPath.startsWith(bgRoot+path.sep))throw new Error('底图路径必须位于项目 assets/backgrounds 内');
    const bg=await fs.readFile(bgPath),native=await sharp(bg).metadata();
    const requestIds=new Set(p.elements.flatMap(elementIds));
    const fingerprint=hash(JSON.stringify([p,rendererHash,hash(bg),[...requestIds].map(id=>glyphs[id])]));
    const files={png:`output/png/${p.id}.png`,outlinedSVG:`output/svg-outlined/${p.id}.svg`,editableSVG:`output/svg-editable/${p.id}.svg`,preview:`review/${p.id}.png`};
    if(!options.force&&pageCache[p.id]===fingerprint&&records.has(p.id)&&(await Promise.all(Object.values(files).map(f=>exists(path.join(project,f))))).every(Boolean)){
      console.log('Reused '+p.id);continue;
    }
    const head=`<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="${p.ew}" height="${p.eh}" viewBox="0 0 ${p.w} ${p.h}"><title>${esc(p.label)}</title><desc>正文、标题与数字保留矢量，背景为嵌入位图。</desc><image x="0" y="0" width="${p.w}" height="${p.h}" preserveAspectRatio="none" xlink:href="data:image/png;base64,${bg.toString('base64')}"/>`;
    const bounds=[];
    for(const editable of [false,true]){
      const svg=head+p.elements.map(e=>e.type==='text'?textElement(e,editable,bounds):e.type==='inline'?inlineElement(e,editable,bounds):e.type==='art'?artElement(e,editable?null:bounds):e.markup).join('')+'</svg>';
      await fs.writeFile(path.join(project,editable?files.editableSVG:files.outlinedSVG),svg);
      if(!editable){
        await sharp(Buffer.from(svg),{limitInputPixels:false}).withMetadata({density:300}).png({compressionLevel:9}).toFile(path.join(project,files.png));
        await sharp(Buffer.from(svg),{limitInputPixels:false}).resize({width:Math.min(p.w,1280)}).png().toFile(path.join(project,files.preview));
      }
    }
    await fs.writeFile(path.join(project,'review',p.id+'-text-bounds.json'),JSON.stringify(bounds,null,2));
    records.set(p.id,{id:p.id,label:p.label,...files,width:p.ew,height:p.eh,nativeWidth:native.width,nativeHeight:native.height});
    pageCache[p.id]=fingerprint;console.log('Exported '+p.id);
  }
  await fs.writeFile(path.join(cacheDir,'pages.json'),JSON.stringify(pageCache,null,2));
  await fs.writeFile(path.join(project,'output/manifest.json'),JSON.stringify([...records.values()],null,2));
  state.lastRenderedPages=selection;state.stage='vector-typeset';
  await fs.writeFile(path.join(project,'campaign-state.json'),JSON.stringify(state,null,2));
  return [...records.values()];
}
