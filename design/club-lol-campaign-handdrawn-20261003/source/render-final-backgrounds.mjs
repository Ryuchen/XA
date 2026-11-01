import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const sharp=require(process.argv[2]+'/sharp');
const project=path.resolve(import.meta.dirname,'..');
const plan=JSON.parse(fs.readFileSync(path.join(project,'layout-final-plan.json'),'utf8'));
const content=JSON.parse(fs.readFileSync(path.join(project,'content.json'),'utf8'));
const environment=JSON.parse(fs.readFileSync(path.join(project,'source/background-generation-prompts.json'),'utf8'));
const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const read=f=>fs.readFileSync(path.join(project,f));
const svg=(w,h,body)=>Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="'+w+'" height="'+h+'">'+body+'</svg>');
const write=(f,data)=>fs.writeFileSync(path.join(project,f),data);
for(const a of environment.assets){a.native=await sharp(read(a.file)).metadata().then(m=>({width:m.width,height:m.height}));}
write('assets/environment/manifest.json',JSON.stringify(environment,null,2)+'\n');
const assets=Object.fromEntries(environment.assets.map(a=>[a.id,read(a.file)]));
const heroes={duo:read('assets/illustrations/01-ez-lux-duo.png'),victory:read('assets/illustrations/02-ez-lux-victory.png')};
const nativeHero={width:1536,height:1024};
const panel=(x,y,w,h,accent,header=0)=>'<rect x="'+(x+4)+'" y="'+(y+7)+'" width="'+w+'" height="'+h+'" rx="25" fill="#dfe9e8" opacity=".55"/><path d="M'+(x+24)+' '+y+' Q'+x+' '+(y-1)+' '+x+' '+(y+24)+' L'+(x+1)+' '+(y+h-24)+' Q'+x+' '+(y+h+2)+' '+(x+24)+' '+(y+h)+' L'+(x+w-26)+' '+(y+h-1)+' Q'+(x+w+1)+' '+(y+h)+' '+(x+w)+' '+(y+h-25)+' L'+(x+w-1)+' '+(y+23)+' Q'+(x+w+1)+' '+(y-1)+' '+(x+w-25)+' '+y+' Z" fill="#fffdf7" stroke="#89a2af" stroke-width="2.2"/>'+(header?'<path d="M'+(x+24)+' '+y+'H'+(x+w-24)+'Q'+(x+w)+' '+y+' '+(x+w)+' '+(y+24)+'V'+(y+header)+'H'+x+'V'+(y+24)+'Q'+x+' '+y+' '+(x+24)+' '+y+'Z" fill="#17334b"/>':'')+'<path d="M'+(x+17)+' '+(y+h-18)+' Q'+(x+37)+' '+(y+h-23)+' '+(x+51)+' '+(y+h-18)+'" fill="none" stroke="'+accent+'" stroke-width="3" stroke-linecap="round"/>';
function blockMarkup(p,b){
 let body='';
 if(b.kind==='table'){
  body+=panel(b.x,b.y,b.w,b.h,p.accent,b.headerHeight||80);
  const rh=(b.h-(b.headerHeight||80))/b.rows;
  for(let i=1;i<b.rows;i++){const y=b.y+(b.headerHeight||80)+i*rh;body+='<path d="M'+(b.x+28)+' '+y+' Q'+(b.x+b.w/2)+' '+(y+1)+' '+(b.x+b.w-28)+' '+y+'" fill="none" stroke="#cbdadd" stroke-width="1.8"/>';}
  return body;
 }
 if(b.kind==='parallel-tables'){
  const gap=24,cw=(b.w-gap)/2;
  for(let n=0;n<2;n++){
   const x=b.x+n*(cw+gap),header=b.headerHeight||100;
   body+=panel(x,b.y,cw,b.h,p.accent,header);
   const rows=b.rowsByCard[n],rh=(b.h-header)/rows;
   for(let j=1;j<rows;j++){const y=b.y+header+rh*j;body+='<path d="M'+(x+24)+' '+y+'H'+(x+cw-24)+'" stroke="#cbdadd" stroke-width="1.8"/>';}
  }
  return body;
 }
 if(b.kind==='cards'){
  const cols=b.columns||2,rows=Math.ceil(b.cards/cols),gap=24,cw=(b.w-gap*(cols-1))/cols,ch=(b.h-gap*(rows-1))/rows;
  for(let n=0;n<b.cards;n++){
   const x=b.x+(n%cols)*(cw+gap),y=b.y+Math.floor(n/cols)*(ch+gap);
   body+=panel(x,y,cw,ch,p.accent);
   body+='<path d="M'+(x+24)+' '+(y+16)+'H'+(x+cw-24)+'" stroke="'+p.accent+'" stroke-width="4" stroke-linecap="round" opacity=".8"/>';
  }
  return body;
 }
 if(b.kind==='offer'){
  body+=panel(b.x,b.y,b.w,b.h,p.accent);
  body+='<rect x="'+(b.x+16)+'" y="'+(b.y+16)+'" width="'+(b.w-32)+'" height="'+(b.h-32)+'" rx="20" fill="'+p.accent+'" opacity=".10"/><path d="M'+(b.x+14)+' '+(b.y+b.h/2)+'H'+(b.x+b.w-14)+'" stroke="'+p.accent+'" stroke-width="2" opacity=".25" stroke-dasharray="5 14"/>';
  return body;
 }
 body+=panel(b.x,b.y,b.w,b.h,p.accent);
 if(b.kind==='notes')body+='<path d="M'+(b.x+32)+' '+(b.y+86)+'H'+(b.x+b.w-32)+'" stroke="'+p.accent+'" stroke-width="2" opacity=".7"/>';
 return body;
}
function headerDecoration(p){
 const w=p.w;
 return '<path d="M64 120Q'+(w*.25)+' 116 642 120" fill="none" stroke="'+p.accent+'" stroke-width="3.2" stroke-linecap="round"/>';
}
function guideMarkup(p){
 let out='';
 const font='font-family="Microsoft YaHei UI,sans-serif"';
 if(p.type==='cover'){
  for(const [name,r] of Object.entries(p.copyZones)){out+='<rect x="'+r.x+'" y="'+r.y+'" width="'+r.w+'" height="'+r.h+'" rx="12" fill="#ffffff" fill-opacity=".68" stroke="#476c86" stroke-width="2" stroke-dasharray="9 7"/><text x="'+(r.x+r.w/2)+'" y="'+(r.y+r.h/2+12)+'" text-anchor="middle" '+font+' font-size="34" fill="#17334b">'+({brand:'品牌居中区',title:'主标题居中区',core:'卖点 / 活动居中区',version:'平台条居中区'}[name])+'</text>';}
 }else{
  out+='<text x="64" y="85" '+font+' font-size="32" fill="#17334b">结构稿 · '+esc(p.label)+'</text>';
  out+='<text x="350" y="240" text-anchor="middle" '+font+' font-size="44" font-weight="700" fill="#17334b">标题净文字区</text><text x="350" y="345" text-anchor="middle" '+font+' font-size="26" fill="#476c86">右侧插画独立避让 · 保留平台 / 单位区</text>';
  for(const b of p.blocks){
   if(b.kind==='cards'){
    const cols=b.columns||2,rows=Math.ceil(b.cards/cols),gap=24,cw=(b.w-gap*(cols-1))/cols,ch=(b.h-gap*(rows-1))/rows;
    let names=Array.from({length:b.cards},(_,n)=>'净文字区 '+(n+1));
    if(b.id==='fixed-tiers')names=['T0 档','T1 档'];
    if(b.id==='qualification'&&b.cards===4)names=['T0 顶尖','T1 专业','T2 招牌','T3 新秀'];
    if(b.id==='qualification'&&b.cards===2)names=['资质 / 档位 1','资质 / 档位 2'];
    if(b.id==='tiers'&&b.cards===4)names=p.setId==='03-arena'?['T3 娱乐','T2 技术','T1 招牌','T0 顶尖']:['T0 档','T1 档','T2 / T2Pro','T3 档'];
    for(let n=0;n<b.cards;n++){
     const x=b.x+(n%cols)*(cw+gap),y=b.y+Math.floor(n/cols)*(ch+gap);
     out+='<text x="'+(x+cw/2)+'" y="'+(y+48)+'" text-anchor="middle" '+font+' font-size="30" font-weight="700" fill="#17334b">'+esc(names[n])+'</text><text x="'+(x+cw/2)+'" y="'+(y+ch/2+30)+'" text-anchor="middle" '+font+' font-size="24" fill="#6a8390">所属卡片内居中</text>';
    }
    continue;
   }
   if(b.kind==='parallel-tables'){
    const gap=24,cw=(b.w-gap)/2;
    for(let n=0;n<2;n++){const x=b.x+n*(cw+gap);out+='<text x="'+(x+cw/2)+'" y="'+(b.y+58)+'" text-anchor="middle" '+font+' font-size="32" font-weight="700" fill="#fffdf7">'+(n===0?'女陪区':'男陪区')+'</text><text x="'+(x+cw/2)+'" y="'+(b.y+150)+'" text-anchor="middle" '+font+' font-size="26" fill="#6a8390">'+b.rowsByCard[n]+' 行 · 档位 / 金额</text>';}
    continue;
   }
   out+='<text x="'+(b.x+b.w/2)+'" y="'+(b.y+54)+'" text-anchor="middle" '+font+' font-size="'+(b.kind==='table'||b.kind==='parallel-tables'?32:30)+'" font-weight="700" fill="'+(b.kind==='table'||b.kind==='parallel-tables'?'#fffdf7':'#17334b')+'">'+esc(b.label)+'</text>';
   const note=b.kind==='table'?b.rows+'行 · '+b.columns+'列 · 价目与单位共基线':b.kind==='parallel-tables'?'左右 '+b.rowsByCard.join(' / ')+' 行':b.kind==='cards'?b.cards+'张卡片 · 各自净文字区居中':b.kind==='notes'?'常规说明＋选择性重点 · 预留上下间距':'独立核心信息区';
   out+='<text x="'+(b.x+b.w/2)+'" y="'+(b.y+(b.kind==='table'?115:b.h/2+42))+'" text-anchor="middle" '+font+' font-size="26" fill="#6a8390">'+esc(note)+'</text>';
  }
 }
 return out;
}
const manifest=[],checks=[];
for(const p of plan.pages.filter(p=>["01-solo-duo-price","01-solo-duo-advert","02-flex-price","02-flex-advert","03-arena-price","04-boost-price","04-boost-advert","05-lp-price","06-overseas-price","06-overseas-advert","07-day-price","07-day-advert","08-haidou-price","09-placement-price","09-placement-advert","10-maintenance-price","10-maintenance-advert"].includes(p.id))){
 let base=await sharp({create:{width:p.w,height:p.h,channels:4,background:'#fffbf3'}}).png().toBuffer();
 const layers=[];
 if(p.type==='cover'){
  layers.push({input:await sharp(assets[p.theme]).resize(p.w,p.h,{fit:'fill'}).png().toBuffer(),left:0,top:0});
  const zones=p.copyZones;
  const overlay='<defs><linearGradient id="copyfade"><stop offset="0" stop-color="#fffbf3" stop-opacity=".50"/><stop offset=".78" stop-color="#fffbf3" stop-opacity=".40"/><stop offset="1" stop-color="#fffbf3" stop-opacity="0"/></linearGradient></defs><rect width="850" height="790" fill="url(#copyfade)"/><path d="M248 454Q430 448 610 454" stroke="'+p.accent+'" fill="none" stroke-width="4" stroke-linecap="round"/>'+panel(zones.version.x,zones.version.y,zones.version.w,zones.version.h,p.accent);
  layers.push({input:svg(p.w,p.h,overlay),left:0,top:0});
 }else{
  const scenery=await sharp(assets[p.theme]).resize({width:p.w}).png().toBuffer();
  const sceneryMeta=await sharp(scenery).metadata();
  layers.push({input:scenery,left:0,top:0});
  const fadeY=430,fadeH=sceneryMeta.height-fadeY;
  layers.push({input:svg(p.w,p.h,'<defs><linearGradient id="headerfade" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fffbf3" stop-opacity="0"/><stop offset="1" stop-color="#fffbf3" stop-opacity="1"/></linearGradient></defs><rect x="0" y="'+fadeY+'" width="'+p.w+'" height="'+fadeH+'" fill="url(#headerfade)"/>'),left:0,top:0});
  const footer=await sharp(assets.footer).resize({width:p.w}).png().toBuffer();
  const footerMeta=await sharp(footer).metadata();
  layers.push({input:footer,left:0,top:p.h-footerMeta.height});
  layers.push({input:svg(p.w,p.h,'<defs><linearGradient id="footerfade" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fffbf3" stop-opacity="1"/><stop offset="1" stop-color="#fffbf3" stop-opacity="0"/></linearGradient></defs><rect x="0" y="'+(p.h-footerMeta.height)+'" width="'+p.w+'" height="120" fill="url(#footerfade)"/>'),left:0,top:0});
  const body=headerDecoration(p)+p.blocks.map(b=>blockMarkup(p,b)).join('');
  layers.push({input:svg(p.w,p.h,body),left:0,top:0});
 }
 const heroBox=p.type==='cover'?p.heroBox:p.header.heroBox;
 const hero=await sharp(heroes[p.hero]).resize(heroBox.w,heroBox.h,{fit:'contain',background:{r:0,g:0,b:0,alpha:0}}).png().toBuffer();
 layers.push({input:hero,left:heroBox.x,top:heroBox.y});
 base=await sharp(base).composite(layers).png().toBuffer();
 const file='assets/backgrounds/'+p.id+'-bg-r2.png';
 write(file,base);
 const small=await sharp(base).resize({width:390}).png().toBuffer();
 write('review/backgrounds/'+p.id+'-390.png',small);
 const guide=await sharp(base).composite([{input:svg(p.w,p.h,guideMarkup(p)),left:0,top:0}]).png().toBuffer();
 write('source/layout-guides/'+p.id+'-guide-r2.png',guide);
 const failed=[];
 for(const b of p.blocks)if(b.x<0||b.y<0||b.x+b.w>p.w||b.y+b.h>p.h)failed.push('container out of canvas: '+b.id);
 for(let i=1;i<p.blocks.length;i++)if(p.blocks[i].y<p.blocks[i-1].y+p.blocks[i-1].h)failed.push('container overlap: '+p.blocks[i].id);
 const leftEnd=p.type==='cover'?784:644;
 if(heroBox.x-leftEnd<24)failed.push('header copy/illustration gap below 24');
 if(heroBox.x+heroBox.w>p.w||heroBox.y+heroBox.h>p.h)failed.push('hero out of canvas');
 checks.push({id:p.id,failed,copyToHeroGap:heroBox.x-leftEnd,containers:p.blocks.length,textSafeAreas:p.textSafeAreas.length});
 manifest.push({id:p.id,label:p.label,type:p.type,background:file,w:p.w,h:p.h,finalPlannedWidth:p.ew,finalPlannedHeight:p.eh,environment:p.theme,hero:p.hero,guide:'source/layout-guides/'+p.id+'-guide-r2.png',preview:'review/backgrounds/'+p.id+'-390.png'});
}
write('assets/backgrounds/manifest-bg-r2.json',JSON.stringify({revision:'bg-r2',stage:'awaiting-user',environmentNative:environment.assets.map(a=>({id:a.id,...a.native})),heroNative:nativeHero,pages:manifest},null,2)+'\n');
write('review/background-layout-checks-r2.json',JSON.stringify(checks,null,2)+'\n');
